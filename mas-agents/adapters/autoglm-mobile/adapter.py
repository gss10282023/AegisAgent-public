from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

from mas_harness.evidence import EvidenceWriter, stable_sha256
from mas_harness.examples import ToyEnv
from mas_harness.oracles import make_oracle
from mas_harness.oracles.zoo.base import OracleContext, decision_from_evidence
from mas_harness.oracles.zoo.settings.boot_health import capture_device_infra
from mas_harness.oracles.zoo.utils.time_window import capture_episode_time
from mas_harness.runtime import reset_for_episode
from mas_harness.runtime.android.env_adapter import AndroidEnvAdapter
from mas_harness.spec.spec_loader import (
    discover_case,
    load_schema,
    load_yaml_or_json,
    validate_against_schema,
)

_ADAPTER_ROOT = Path(__file__).resolve().parent


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _env_int(name: str, default: int | None = None) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = str(value).strip()
    if not value:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _png_size_px(png_bytes: bytes) -> tuple[int | None, int | None]:
    # Parse PNG IHDR width/height (no external deps).
    if not isinstance(png_bytes, (bytes, bytearray)):
        return None, None
    data = bytes(png_bytes)
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None, None
    try:
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
    except Exception:
        return None, None
    if w <= 0 or h <= 0:
        return None, None
    return int(w), int(h)


def _resolve_open_autoglm_dir() -> Path:
    override = _env_str("MAS_AUTOGLM_SRC_DIR")
    if override:
        return Path(override).expanduser().resolve()

    # Default: keep Open-AutoGLM-main as a black box under mas-agents/adapters/autoglm/.
    return (_ADAPTER_ROOT.parent / "autoglm" / "Open-AutoGLM-main").resolve()


def _ensure_open_autoglm_importable() -> None:
    open_autoglm_dir = _resolve_open_autoglm_dir()
    if (open_autoglm_dir / "phone_agent").is_dir() and str(open_autoglm_dir) not in sys.path:
        # Prefer the repo-vendored Open-AutoGLM-main (treated as a black box).
        sys.path.insert(0, str(open_autoglm_dir))

    try:
        import phone_agent  # noqa: F401
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "phone_agent import failed. Ensure Open-AutoGLM is present at "
            f"{open_autoglm_dir} (or set MAS_AUTOGLM_SRC_DIR)."
        ) from e


@dataclass
class _NoopActionResult:
    success: bool
    should_finish: bool
    message: str | None = None
    requires_confirmation: bool = False


class _NoopActionHandler:
    def execute(
        self,
        action: dict[str, Any],
        screen_width: int,
        screen_height: int,
    ) -> _NoopActionResult:
        del screen_width, screen_height
        meta = action.get("_metadata")
        if meta == "finish":
            message = action.get("message")
            return _NoopActionResult(True, True, message if isinstance(message, str) else None)
        if meta != "do":
            return _NoopActionResult(False, False, f"Unknown action type: {meta}")
        return _NoopActionResult(True, False)


class _Tee:
    def __init__(self, *streams: Any):
        self._streams = [s for s in streams if s is not None]

    def write(self, data: str) -> int:
        wrote = 0
        for stream in self._streams:
            wrote = stream.write(data)
            try:
                stream.flush()
            except Exception:
                pass
        return wrote

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:
                pass


def _parse_wait_ms(raw: Any) -> int:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(max(0.0, float(raw) * 1000.0))
    if not isinstance(raw, str) or not raw.strip():
        return 250
    match = re.search(r"([0-9]+(?:\\.[0-9]+)?)", raw)
    if not match:
        return 250
    try:
        seconds = float(match.group(1))
    except Exception:
        return 250
    return int(max(0.0, seconds * 1000.0))


def _autoglm_action_to_mas_action(
    action: Mapping[str, Any],
    *,
    autoglm_response: Any | None,
    get_package_name_fn,
) -> Dict[str, Any]:
    raw_meta = action.get("_metadata")
    autoglm_meta: Dict[str, Any] = {
        "parsed_action": dict(action) if isinstance(action, Mapping) else action,
    }
    if autoglm_response is not None:
        autoglm_meta["model"] = {
            "thinking": getattr(autoglm_response, "thinking", None),
            "action": getattr(autoglm_response, "action", None),
            "time_to_first_token_s": getattr(autoglm_response, "time_to_first_token", None),
            "time_to_thinking_end_s": getattr(autoglm_response, "time_to_thinking_end", None),
            "total_time_s": getattr(autoglm_response, "total_time", None),
        }

    if raw_meta == "finish":
        message = action.get("message")
        return {
            "type": "finished",
            "message": message if isinstance(message, str) else "",
            "autoglm": autoglm_meta,
        }

    if raw_meta != "do":
        return {
            "type": "wait",
            "duration_ms": 0,
            "note": f"autoglm_unknown_metadata:{raw_meta}",
            "autoglm": autoglm_meta,
        }

    action_name = str(action.get("action") or "").strip()

    if action_name == "Launch":
        app = action.get("app")
        app_name = str(app).strip() if app is not None else ""
        package = get_package_name_fn(app_name) if app_name else None
        return {
            "type": "open_app",
            "package": package or (app_name or None),
            "autoglm": {**autoglm_meta, "autoglm_action_name": action_name, "app": app_name},
        }

    if action_name in {"Tap", "Double Tap", "Long Press"}:
        element = action.get("element")
        coords = list(element) if isinstance(element, (list, tuple)) else []
        x_raw = coords[0] if len(coords) >= 2 else None
        y_raw = coords[1] if len(coords) >= 2 else None
        try:
            x_norm = float(x_raw) / 1000.0
            y_norm = float(y_raw) / 1000.0
        except Exception:
            x_norm = None
            y_norm = None

        mapped_type = "tap"
        extra: Dict[str, Any] = {}
        if action_name == "Long Press":
            mapped_type = "long_press"
            extra["duration_ms"] = 3000
        if action_name == "Double Tap":
            extra["tap_count"] = 2

        out: Dict[str, Any] = {
            "type": mapped_type,
            "coord_space": "normalized_screenshot",
            "x": x_norm,
            "y": y_norm,
            "autoglm": {**autoglm_meta, "autoglm_action_name": action_name},
            **extra,
        }
        return out

    if action_name == "Swipe":
        start = action.get("start")
        end = action.get("end")
        start_xy = list(start) if isinstance(start, (list, tuple)) else []
        end_xy = list(end) if isinstance(end, (list, tuple)) else []
        try:
            sx = float(start_xy[0]) / 1000.0
            sy = float(start_xy[1]) / 1000.0
            ex = float(end_xy[0]) / 1000.0
            ey = float(end_xy[1]) / 1000.0
        except Exception:
            sx = sy = ex = ey = None
        return {
            "type": "swipe",
            "coord_space": "normalized_screenshot",
            "start": {"x": sx, "y": sy},
            "end": {"x": ex, "y": ey},
            "autoglm": {**autoglm_meta, "autoglm_action_name": action_name},
        }

    if action_name == "Type" or action_name == "Type_Name":
        text = action.get("text")
        return {
            "type": "type",
            "text": text if isinstance(text, str) else ("" if text is None else str(text)),
            "autoglm": {**autoglm_meta, "autoglm_action_name": action_name},
        }

    if action_name == "Back":
        return {
            "type": "press_back",
            "autoglm": {**autoglm_meta, "autoglm_action_name": action_name},
        }

    if action_name == "Home":
        return {
            "type": "home",
            "autoglm": {**autoglm_meta, "autoglm_action_name": action_name},
        }

    if action_name == "Wait":
        duration_ms = _parse_wait_ms(action.get("duration"))
        return {
            "type": "wait",
            "duration_ms": int(duration_ms),
            "autoglm": {**autoglm_meta, "autoglm_action_name": action_name},
        }

    if action_name in {"Take_over", "Interact"}:
        message = action.get("message")
        msg = message if isinstance(message, str) else ("" if message is None else str(message))
        return {
            "type": "finished",
            "message": f"autoglm_takeover_required:{msg}".strip(":"),
            "autoglm": {**autoglm_meta, "autoglm_action_name": action_name, "message": msg},
        }

    return {
        "type": "wait",
        "duration_ms": 0,
        "note": f"autoglm_unsupported_action:{action_name or 'missing'}",
        "autoglm": {**autoglm_meta, "autoglm_action_name": action_name},
    }


def _event_from_normalized_action(
    normalized_action: Mapping[str, Any],
    *,
    ref_step_idx: int,
    result: Any,
) -> Dict[str, Any]:
    event_type = str(normalized_action.get("type") or "").strip().lower()
    event: Dict[str, Any] = {
        "timestamp_ms": _utc_ms(),
        "ref_step_idx": int(ref_step_idx),
        "type": event_type,
    }

    ok = None
    error = None
    if isinstance(result, Mapping):
        ok = result.get("ok")
        error = result.get("error")
    if isinstance(ok, bool):
        event["ok"] = ok
    if error is not None:
        event["error"] = str(error)

    if event_type in {"tap", "long_press"}:
        coord = normalized_action.get("coord")
        coord_obj = dict(coord) if isinstance(coord, Mapping) else {}
        x_px = coord_obj.get("x_px")
        y_px = coord_obj.get("y_px")
        event["coord_space"] = "physical_px"
        event["x"] = x_px if isinstance(x_px, int) else None
        event["y"] = y_px if isinstance(y_px, int) else None
        duration_ms = normalized_action.get("duration_ms")
        if isinstance(duration_ms, int) and event_type == "long_press":
            event["duration_ms"] = int(max(0, duration_ms))

    elif event_type == "swipe":
        start = normalized_action.get("start")
        end = normalized_action.get("end")
        start_obj = dict(start) if isinstance(start, Mapping) else {}
        end_obj = dict(end) if isinstance(end, Mapping) else {}
        event["coord_space"] = "physical_px"
        event["start"] = {"x": start_obj.get("x_px"), "y": start_obj.get("y_px")}
        event["end"] = {"x": end_obj.get("x_px"), "y": end_obj.get("y_px")}
        duration_ms = normalized_action.get("duration_ms")
        if isinstance(duration_ms, int):
            event["duration_ms"] = int(max(0, duration_ms))

    elif event_type == "type":
        text = normalized_action.get("text")
        event["text"] = text if isinstance(text, str) else ("" if text is None else str(text))

    elif event_type == "press_back":
        event["type"] = "back"

    elif event_type == "open_app":
        pkg = normalized_action.get("package")
        pkg_str = str(pkg).strip() if pkg is not None else ""
        event["package"] = pkg_str or None

    elif event_type == "open_url":
        url = normalized_action.get("url")
        url_str = str(url).strip() if url is not None else ""
        event["url"] = url_str or None

    elif event_type == "wait":
        duration_ms = normalized_action.get("duration_ms")
        if isinstance(duration_ms, int):
            event["duration_ms"] = int(max(0, duration_ms))

    elif event_type in {"finished", "home"}:
        pass

    else:
        event["type"] = "wait"
        event["raw_type"] = event_type or "missing"

    return event


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(_safe_json(obj))
        f.write("\n")


class AutoGLMMobileAdapter:
    def run_case(self, *, case_dir: Path, evidence_dir: Path, ctx) -> Dict[str, Any]:
        paths = discover_case(case_dir)

        task_schema = load_schema(ctx.schemas_dir / "task_schema.json")
        policy_schema = load_schema(ctx.schemas_dir / "policy_schema.json")
        eval_schema = load_schema(ctx.schemas_dir / "eval_schema.json")
        attack_schema = load_schema(ctx.schemas_dir / "attack_schema.json")

        task = load_yaml_or_json(paths.task)
        policy = load_yaml_or_json(paths.policy)
        ev = load_yaml_or_json(paths.eval)

        validate_against_schema(task, task_schema, where=str(paths.task))
        validate_against_schema(policy, policy_schema, where=str(paths.policy))
        validate_against_schema(ev, eval_schema, where=str(paths.eval))
        if paths.attack is not None:
            attack = load_yaml_or_json(paths.attack)
            validate_against_schema(attack, attack_schema, where=str(paths.attack))

        case_id = str(task.get("task_id") or case_dir.name)
        agent_id = str(ctx.registry_entry.get("agent_id") or "autoglm-mobile")

        writer = EvidenceWriter(
            run_dir=ctx.output_dir,
            case_id=case_id,
            seed=ctx.seed,
            run_mode="phase3",
            metadata={
                **(ctx.run_metadata or {}),
                "agent_id": agent_id,
                "adapter": "autoglm_mobile",
            },
            episode_dir=evidence_dir,
            ui_dump_every_n=1,
        )

        has_android = bool(getattr(ctx.phase0_cfg, "android_serial", None))
        if has_android:
            env: Any = AndroidEnvAdapter(
                adb_path=str(getattr(ctx.phase0_cfg, "adb_path", "adb")),
                serial=str(getattr(ctx.phase0_cfg, "android_serial")),
                evidence_writer=None,
                action_trace_level=None,
            )
        else:
            env = ToyEnv()

        oracle_cfg = task.get("success_oracle") or task.get("oracle") or {}
        oracle = make_oracle(oracle_cfg)
        oracle_id = getattr(oracle, "oracle_id", str(oracle_cfg.get("type", "unknown")))
        oracle_type = getattr(oracle, "oracle_type", "unknown")

        steps_executed = 0
        terminated_reason = "unknown"
        infra_analysis: Dict[str, Any] = {"infra_failed": False, "infra_failure_reasons": []}
        error: Any = None
        decision: Dict[str, Any] = {
            "success": False,
            "conclusive": False,
            "score": 0.0,
            "reason": "not_run",
        }
        episode_time = None

        agent_events_path_run = ctx.output_dir / "agent_events.jsonl"
        agent_events_path_evidence = evidence_dir / "agent_events.jsonl"

        autoglm_dir = evidence_dir / "autoglm_traces"
        terminal_log_path = autoglm_dir / "terminal.log"
        model_trace_path = autoglm_dir / "model_trace.jsonl"

        try:
            agent_events_path_run.write_text("", encoding="utf-8")
            autoglm_dir.mkdir(parents=True, exist_ok=True)
            terminal_log_path.write_text("", encoding="utf-8")
            model_trace_path.write_text("", encoding="utf-8")
        except Exception:
            pass

        try:
            # IMPORTANT: Do not run MAS executor actions as part of reset. For
            # AutoGLM-Mobile we only capture evidence; agent actions are executed
            # by Open-AutoGLM via ADB. We still allow snapshot loading.
            reset_controller = env
            if has_android and hasattr(env, "_controller"):
                reset_controller = getattr(env, "_controller")

            reset_event = reset_for_episode(
                controller=reset_controller,
                initial_state=task.get("initial_state"),
                reset_strategy=ctx.phase0_cfg.reset_strategy,
                snapshot_tag=ctx.phase0_cfg.snapshot_tag,
            )
            writer.record_reset(reset_event)

            if has_android:
                episode_time, episode_time_event = capture_episode_time(
                    controller=env,
                    task_spec=task,
                )
                writer.record_device_event(episode_time_event)

                infra_event, infra_analysis = capture_device_infra(
                    env,
                    device_epoch_time_ms=episode_time.t0_device_epoch_ms if episode_time else None,
                )
                writer.record_device_event(infra_event)

            oracle_ctx = OracleContext.from_task_and_controller(
                task_spec=task,
                controller=env,
                episode_time=episode_time,
                episode_dir=writer.root,
            )
            writer.record_oracle_events(oracle.pre_check(oracle_ctx))

            user_goal = str(task.get("user_goal") or "")
            max_steps = int(task.get("max_steps", 20))

            if not has_android:
                terminated_reason = "dry_run_no_android"
                obs = env.observe()
                writer.record_observation(step=0, observation=obs)

                action = {"type": "wait", "duration_ms": 0, "note": "dry_run_no_android"}
                input_digest = stable_sha256({"step": 0, "ui_hash": obs.get("ui_hash")})
                response_digest = stable_sha256(action)
                writer.record_agent_call(
                    {
                        "step_idx": 0,
                        "agent_name": agent_id,
                        "provider": None,
                        "model_id": None,
                        "base_url": None,
                        "input_digest": input_digest,
                        "response_digest": response_digest,
                        "latency_ms": 0,
                        "tokens_in": None,
                        "tokens_out": None,
                        "error": None,
                    }
                )
                normalized = writer.record_agent_action(step=0, action=action)
                writer.record_action(
                    step=0,
                    action=normalized,
                    result={"ok": True, "dry_run": True},
                )

                _append_jsonl(
                    agent_events_path_run,
                    {
                        "timestamp_ms": _utc_ms(),
                        "type": "wait",
                        "duration_ms": 0,
                        "ref_step_idx": 0,
                    },
                )
                agent_events_path_evidence.write_text(
                    agent_events_path_run.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                steps_executed = 1
            else:
                try:
                    from dotenv import load_dotenv

                    load_dotenv()
                except Exception:
                    pass

                _ensure_open_autoglm_importable()
                import phone_agent.adb as phone_agent_adb
                from phone_agent import PhoneAgent
                from phone_agent.adb.screenshot import Screenshot
                from phone_agent.agent import AgentConfig
                from phone_agent.config.apps import get_app_name, get_package_name
                from phone_agent.model import ModelConfig

                base_url = (
                    _env_str("PHONE_AGENT_BASE_URL")
                    or _env_str("AUTOGLM_BASE_URL")
                    or _env_str("MAS_AGENT_BASE_URL")
                    or "https://open.bigmodel.cn/api/paas/v4"
                )
                model_name = (
                    _env_str("PHONE_AGENT_MODEL")
                    or _env_str("AUTOGLM_MODEL_NAME")
                    or _env_str("MAS_AGENT_MODEL_ID")
                    or "autoglm-phone"
                )
                api_key = (
                    _env_str("PHONE_AGENT_API_KEY")
                    or _env_str("AUTOGLM_API_KEY")
                    or _env_str("OPENAI_API_KEY")
                    or "EMPTY"
                )

                max_tokens = int(
                    _env_int(
                        "PHONE_AGENT_MAX_TOKENS",
                        _env_int("MAS_AGENT_MAX_TOKENS", 3000),
                    )
                    or 3000
                )
                temperature = float(
                    _env_str(
                        "PHONE_AGENT_TEMPERATURE",
                        _env_str("MAS_AGENT_TEMPERATURE", "0.0"),
                    )
                    or 0.0
                )
                top_p = float(
                    _env_str("PHONE_AGENT_TOP_P", _env_str("MAS_AGENT_TOP_P", "0.85")) or 0.85
                )
                frequency_penalty = float(_env_str("PHONE_AGENT_FREQUENCY_PENALTY", "0.2") or 0.2)
                lang = str(_env_str("PHONE_AGENT_LANG", _env_str("AUTOGLM_LANG", "en")) or "en")

                model_config = ModelConfig(
                    base_url=base_url,
                    model_name=model_name,
                    api_key=api_key,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    frequency_penalty=frequency_penalty,
                    lang=lang,
                )

                agent_config = AgentConfig(
                    max_steps=max_steps,
                    verbose=_env_bool("PHONE_AGENT_VERBOSE", _env_bool("AUTOGLM_VERBOSE", True)),
                    lang=lang,
                    device_id=str(getattr(ctx.phase0_cfg, "android_serial")),
                )

                agent = PhoneAgent(model_config=model_config, agent_config=agent_config)

                # Capture the last ModelResponse from ModelClient.request for structured logs.
                orig_request = agent.model_client.request

                def _request_and_store(messages: list[dict[str, Any]]):
                    resp = orig_request(messages)
                    setattr(agent, "_mas_last_model_response", resp)
                    return resp

                agent.model_client.request = _request_and_store  # type: ignore[method-assign]

                state: dict[str, Any] = {"obs": None}

                def _patched_get_screenshot(
                    device_id: str | None = None,
                    timeout: int = 10,
                ) -> Screenshot:
                    del device_id, timeout
                    obs = state.get("obs") or {}
                    png = obs.get("screenshot_png") if isinstance(obs, dict) else None
                    png_bytes = bytes(png) if isinstance(png, (bytes, bytearray)) else b""
                    w, h = _png_size_px(png_bytes)
                    if w is None or h is None:
                        w, h = 1080, 2400
                    return Screenshot(
                        base64_data=base64.b64encode(png_bytes).decode("utf-8"),
                        width=int(w),
                        height=int(h),
                        is_sensitive=False,
                    )

                def _patched_get_current_app(device_id: str | None = None) -> str:
                    del device_id
                    obs = state.get("obs") or {}
                    fg = obs.get("foreground") if isinstance(obs, dict) else None
                    pkg = fg.get("package") if isinstance(fg, dict) else None
                    if isinstance(pkg, str) and pkg.strip():
                        name = get_app_name(pkg.strip())
                        return name or pkg.strip()
                    return "System Home"

                @contextlib.contextmanager
                def _patch_device_io():
                    orig_screenshot = phone_agent_adb.get_screenshot
                    orig_app = phone_agent_adb.get_current_app
                    phone_agent_adb.get_screenshot = _patched_get_screenshot  # type: ignore[assignment]
                    phone_agent_adb.get_current_app = _patched_get_current_app  # type: ignore[assignment]
                    try:
                        yield
                    finally:
                        phone_agent_adb.get_screenshot = orig_screenshot  # type: ignore[assignment]
                        phone_agent_adb.get_current_app = orig_app  # type: ignore[assignment]

                tee_stdout = _env_bool("MAS_AUTOGLM_STDOUT", True)
                terminated_reason = "max_steps"
                for step in range(max_steps):
                    obs = env.observe(step=step, dump_ui=True)
                    writer.record_observation(step=step, observation=obs)

                    state["obs"] = obs

                    with _patch_device_io():
                        with terminal_log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"\n\n# --- step {step} ---\n")
                            log_file.flush()
                            tee = _Tee(log_file, sys.__stdout__ if tee_stdout else None)
                            with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                                if step == 0:
                                    step_result = agent.step(user_goal)
                                else:
                                    step_result = agent.step()

                    autoglm_response = getattr(agent, "_mas_last_model_response", None)
                    if autoglm_response is not None:
                        _append_jsonl(
                            model_trace_path,
                            {
                                "timestamp_ms": _utc_ms(),
                                "step_idx": step,
                                "thinking": getattr(autoglm_response, "thinking", None),
                                "action": getattr(autoglm_response, "action", None),
                                "raw_content": getattr(autoglm_response, "raw_content", None),
                                "time_to_first_token_s": getattr(
                                    autoglm_response, "time_to_first_token", None
                                ),
                                "time_to_thinking_end_s": getattr(
                                    autoglm_response, "time_to_thinking_end", None
                                ),
                                "total_time_s": getattr(autoglm_response, "total_time", None),
                            },
                        )

                    screenshot_sha256 = stable_sha256(obs.get("screenshot_png") or b"")
                    input_digest = stable_sha256(
                        {
                            "case_id": case_id,
                            "step_idx": step,
                            "ui_hash": obs.get("ui_hash"),
                            "obs_digest": writer.last_obs_digest,
                            "screenshot_sha256": screenshot_sha256,
                        }
                    )
                    response_digest = stable_sha256(
                        {
                            "autoglm_action": getattr(step_result, "action", None),
                            "raw_content": getattr(autoglm_response, "raw_content", None),
                        }
                    )
                    latency_ms = None
                    if autoglm_response is not None:
                        total_time = getattr(autoglm_response, "total_time", None)
                        if isinstance(total_time, (int, float)) and not isinstance(
                            total_time, bool
                        ):
                            latency_ms = int(round(float(total_time) * 1000.0))

                    writer.record_agent_call(
                        {
                            "step_idx": step,
                            "agent_name": agent_id,
                            "provider": getattr(ctx.phase0_cfg, "agent_provider", None),
                            "model_id": model_config.model_name,
                            "base_url": model_config.base_url,
                            "input_digest": input_digest,
                            "response_digest": response_digest,
                            "latency_ms": latency_ms,
                            "tokens_in": None,
                            "tokens_out": None,
                            "error": None,
                        }
                    )

                    autoglm_action = getattr(step_result, "action", None)
                    if not isinstance(autoglm_action, Mapping):
                        autoglm_action = {
                            "_metadata": "finish",
                            "message": "autoglm_action_missing",
                        }

                    raw_action = _autoglm_action_to_mas_action(
                        autoglm_action,
                        autoglm_response=autoglm_response,
                        get_package_name_fn=get_package_name,
                    )
                    normalized_action = writer.record_agent_action(step=step, action=raw_action)

                    if normalized_action.get("type") in {"finished", "stop"}:
                        writer.record_action(
                            step=step,
                            action=normalized_action,
                            result={
                                "ok": bool(getattr(step_result, "success", True)),
                                "note": "finished",
                                "message": getattr(step_result, "message", None),
                            },
                        )
                        _append_jsonl(
                            agent_events_path_run,
                            _event_from_normalized_action(
                                normalized_action,
                                ref_step_idx=step,
                                result={
                                    "ok": bool(getattr(step_result, "success", True)),
                                    "note": "finished",
                                    "message": getattr(step_result, "message", None),
                                },
                            ),
                        )
                        terminated_reason = "agent_stop"
                        break

                    # NOTE: AutoGLM executes actions via its own ADB-based ActionHandler.
                    # We only record evidence here; do NOT run actions via MAS executor.
                    result = {
                        "ok": bool(getattr(step_result, "success", False)),
                        "message": getattr(step_result, "message", None),
                    }
                    writer.record_action(step=step, action=normalized_action, result=result)
                    _append_jsonl(
                        agent_events_path_run,
                        _event_from_normalized_action(
                            normalized_action,
                            ref_step_idx=step,
                            result=result,
                        ),
                    )
                    steps_executed += 1

                agent_events_path_evidence.write_text(
                    agent_events_path_run.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

            post_evidence = oracle.post_check(oracle_ctx)
            writer.record_oracle_events(post_evidence)
            decision = decision_from_evidence(
                post_evidence,
                oracle_id=getattr(oracle, "oracle_id", None),
            )

        except Exception as e:
            terminated_reason = "runner_error"
            error = {"type": type(e).__name__, "repr": repr(e)}
            writer.record_device_event({"event": "runner_error", **error})

        # Best-effort: ensure minimal artifacts so evidence audit can run even when the
        # adapter fails early (e.g., missing keys / missing Open-AutoGLM deps).
        try:
            if writer.last_obs_digest is None:
                try:
                    obs = env.observe(step=0, dump_ui=True)
                except TypeError:
                    obs = env.observe()
                writer.record_observation(step=0, observation=obs)

                noop_action: Dict[str, Any] = {
                    "type": "wait",
                    "duration_ms": 0,
                    "note": "autoglm_fallback_noop",
                    "error": error,
                }
                input_digest = stable_sha256(
                    {"step": 0, "ui_hash": obs.get("ui_hash"), "obs_digest": writer.last_obs_digest}
                )
                response_digest = stable_sha256(noop_action)
                writer.record_agent_call(
                    {
                        "step_idx": 0,
                        "agent_name": agent_id,
                        "provider": getattr(ctx.phase0_cfg, "agent_provider", None),
                        "model_id": None,
                        "base_url": None,
                        "input_digest": input_digest,
                        "response_digest": response_digest,
                        "latency_ms": 0,
                        "tokens_in": None,
                        "tokens_out": None,
                        "error": error,
                    }
                )
                normalized = writer.record_agent_action(step=0, action=noop_action)
                writer.record_action(
                    step=0,
                    action=normalized,
                    result={"ok": False, "error": "adapter_failed"},
                )

            agent_events_empty = True
            try:
                agent_events_empty = (
                    not agent_events_path_run.exists()
                    or agent_events_path_run.stat().st_size == 0
                    or not agent_events_path_run.read_text(encoding="utf-8").strip()
                )
            except Exception:
                agent_events_empty = True

            if agent_events_empty:
                _append_jsonl(
                    agent_events_path_run,
                    {
                        "timestamp_ms": _utc_ms(),
                        "type": "wait",
                        "duration_ms": 0,
                        "ref_step_idx": 0,
                        "error": error,
                    },
                )

            try:
                agent_events_path_evidence.write_text(
                    agent_events_path_run.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            except Exception:
                pass
        except Exception:
            pass

        success = bool(decision.get("success", False))
        conclusive = bool(decision.get("conclusive", False))
        score = float(decision.get("score", 0.0))
        reason = str(decision.get("reason", ""))

        if infra_analysis.get("infra_failed") is True:
            status = "inconclusive"
            failure_class = "infra_failed"
        elif conclusive and success:
            status = "success"
            failure_class = None
        elif conclusive:
            status = "fail"
            failure_class = "task_failed"
        else:
            status = "inconclusive"
            failure_class = "oracle_inconclusive"

        summary = {
            "status": status,
            "steps": int(steps_executed),
            "terminated_reason": terminated_reason,
            "failure_class": failure_class,
            "task_success": {
                "score": score,
                "success": success,
                "conclusive": conclusive,
                "reason": reason,
                "oracle_id": oracle_id,
                "oracle_type": oracle_type,
            },
            "violations": [],
            "notes": {
                "policy_digest": stable_sha256(policy),
                "adapter_root": str(_ADAPTER_ROOT),
                "agent_events_path_run": str(agent_events_path_run),
                "agent_events_path_evidence": str(agent_events_path_evidence),
                "autoglm_terminal_log": str(terminal_log_path),
                "autoglm_model_trace": str(model_trace_path),
                "error": error,
            },
        }
        summary = writer.write_summary(summary)
        writer.close()
        return summary


def create_adapter() -> AutoGLMMobileAdapter:
    return AutoGLMMobileAdapter()
