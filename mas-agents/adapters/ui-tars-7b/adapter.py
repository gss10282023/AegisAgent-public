from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
if str(_ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_ROOT))


def _try_import_uitars_kit() -> tuple[Any, str]:
    try:
        from ui_tars_7b_kit.action_parser import parse_mobile_output  # type: ignore[import-not-found]
        from ui_tars_7b_kit.prompts import MOBILE_PROMPT_TEMPLATE  # type: ignore[import-not-found]

        return parse_mobile_output, str(MOBILE_PROMPT_TEMPLATE)
    except Exception:
        return _fallback_parse_mobile_output, _FALLBACK_MOBILE_PROMPT_TEMPLATE


_FALLBACK_MOBILE_PROMPT_TEMPLATE = """You are a GUI agent controlling an Android phone.
You are given a user instruction and screenshots (optionally with recent history).
You must perform exactly ONE next action to move toward the goal.

## Output Format (STRICT)
Thought: <Follow the rules below. The FIRST line MUST start with: Obs: ...>
Action: <ONE action from the Action Space below>

## Action Space (ONE per step)
click(point='<point>x y</point>')
long_press(point='<point>x y</point>')
type(content='')  # To submit input, add "\\n" at the end of content.
scroll(point='<point>x y</point>', direction='down or up or right or left')
open_app(app_name='')  # Package name or human-readable name/alias.
drag(start_point='<point>x1 y1</point>', end_point='<point>x2 y2</point>')
press_home()
press_back()
finished(content='...')  # Use escape characters \\' , \\" , and \\n in content.
# Optional: wait(seconds=1.0), hotkey(key='enter|back|home')

## Thought Rules (MANDATORY; use {language})
- FIRST LINE: `Obs: ...` → Summarize what is VISIBLE on screen now (short phrases).
- Then provide these compact sections (each on its own line):
  Goal: <restated instruction in one short sentence>
  UIAnchors: ["exact visible text you may tap", "icon description + location", ...]
  ScreenGuess: <what screen this is, e.g., "Home", "Settings", "Login", "Search results">
  NextTargetHint: <the single UI element you will target next>
  SuccessCheck: <one condition to verify success after the next action>
  FallbackIfNotFound: <ONE safe fallback if target is missing (e.g., back, small scroll, open_app)>

- NEVER invent text. If you cannot see it, write NOT_FOUND.

## User Instruction
{instruction}
"""


def _fallback_parse_mobile_output(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty model output")
    thought = ""
    action_line = None
    for line in text.splitlines():
        if line.lower().startswith("thought:"):
            thought = line.split(":", 1)[1].strip()
        if line.lower().startswith("action:"):
            action_line = line.split(":", 1)[1].strip()
            break
    if not action_line:
        raise ValueError("missing Action: line")
    return {"thought": thought, "raw_action": action_line, "actions": [{"type": "unknown", "params": {}}]}


def _png_size_px(png_bytes: bytes) -> tuple[Optional[int], Optional[int]]:
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
    return w, h


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    s = str(raw).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return None


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return repr(obj)


@dataclass(frozen=True)
class UiTarsConfig:
    provider: str
    model_id: str
    base_url: str
    api_key: Optional[str]
    temperature: float
    top_p: float
    max_tokens: int
    timeout_s: float
    max_retries: int
    backoff_s: float
    history_n: int
    language: str
    scroll_frac: float

    @classmethod
    def from_env(cls) -> "UiTarsConfig":
        base_url = (os.environ.get("MAS_AGENT_BASE_URL") or "https://openrouter.ai/api/v1").strip()
        provider = (os.environ.get("MAS_AGENT_PROVIDER") or "").strip() or (
            "openrouter" if "openrouter.ai" in base_url else "local"
        )
        model_id = (os.environ.get("MAS_AGENT_MODEL_ID") or "bytedance/ui-tars-1.5-7b").strip()
        api_key = (
            _first_env("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY")
            if provider == "openrouter"
            else _first_env("MAS_AGENT_API_KEY")
        )

        return cls(
            provider=provider,
            model_id=model_id,
            base_url=base_url,
            api_key=api_key,
            temperature=_env_float("MAS_AGENT_TEMPERATURE", 0.0),
            top_p=_env_float("MAS_AGENT_TOP_P", 0.9),
            max_tokens=_env_int("MAS_AGENT_MAX_TOKENS", 512),
            timeout_s=_env_float("MAS_AGENT_TIMEOUT_S", 60.0),
            max_retries=_env_int("MAS_AGENT_MAX_RETRIES", 2),
            backoff_s=_env_float("MAS_AGENT_RETRY_BACKOFF_S", 2.0),
            history_n=_env_int("MAS_UITARS_HISTORY_N", 3),
            language=(os.environ.get("MAS_UITARS_LANGUAGE") or "Chinese").strip() or "Chinese",
            scroll_frac=_env_float("MAS_UITARS_SCROLL_FRAC", 0.28),
        )


def _build_messages(
    *,
    prompt_template: str,
    instruction: str,
    language: str,
    screenshot_b64: str,
    history: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    user_prompt = str(prompt_template).format(language=language, instruction=instruction)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": [{"type": "text", "text": "You are a helpful assistant."}]},
        {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
    ]
    for img_b64, resp in history:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }
        )
        messages.append({"role": "assistant", "content": str(resp or "")})
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            ],
        }
    )
    return messages


def _openai_compat_chat_completions(
    *,
    base_url: str,
    api_key: Optional[str],
    payload: dict[str, Any],
    timeout_s: float,
    extra_headers: dict[str, str],
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
        raw = resp.read()
    obj = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(obj, dict):
        raise RuntimeError("invalid response (expected JSON object)")
    return obj


def _call_model_with_retries(
    *,
    cfg: UiTarsConfig,
    messages: list[dict[str, Any]],
    extra_headers: dict[str, str],
) -> tuple[str, Optional[int], Optional[int], int]:
    payload = {
        "model": cfg.model_id,
        "messages": messages,
        "temperature": float(cfg.temperature),
        "top_p": float(cfg.top_p),
        "max_tokens": int(cfg.max_tokens),
    }

    attempt = 0
    last_err: Exception | None = None
    t0 = time.perf_counter()
    while attempt <= max(0, int(cfg.max_retries)):
        try:
            obj = _openai_compat_chat_completions(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                payload=payload,
                timeout_s=cfg.timeout_s,
                extra_headers=extra_headers,
            )
            choices = obj.get("choices")
            if not isinstance(choices, list) or not choices:
                raise RuntimeError("missing choices[] in response")
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            text = "" if content is None else str(content)

            usage = obj.get("usage")
            tokens_in = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            tokens_out = usage.get("completion_tokens") if isinstance(usage, dict) else None
            latency_ms = int(round((time.perf_counter() - t0) * 1000.0))
            return text, _safe_int(tokens_in), _safe_int(tokens_out), latency_ms
        except urllib.error.HTTPError as e:
            last_err = e
            if attempt >= max(0, int(cfg.max_retries)):
                break
        except Exception as e:
            last_err = e
            if attempt >= max(0, int(cfg.max_retries)):
                break

        attempt += 1
        time.sleep(max(0.0, float(cfg.backoff_s)))

    latency_ms = int(round((time.perf_counter() - t0) * 1000.0))
    raise RuntimeError(f"model request failed after retries ({latency_ms}ms): {last_err!r}")


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None or isinstance(v, bool):
            return None
        return int(v)
    except Exception:
        return None


def _map_uitars_action(
    *,
    action_type: str,
    params: dict[str, Any],
    render_w: Optional[int],
    render_h: Optional[int],
    scroll_frac: float,
) -> dict[str, Any]:
    t = str(action_type or "").strip()
    p = dict(params) if isinstance(params, dict) else {}

    if t in {"click", "long_press"}:
        pt = p.get("point")
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            x, y = float(pt[0]), float(pt[1])
        else:
            x, y = None, None
        return {"type": t, "x": x, "y": y, "coord_space": "screenshot_px"}

    if t == "drag":
        sp = p.get("start_point")
        ep = p.get("end_point")
        if isinstance(sp, (list, tuple)) and len(sp) == 2 and isinstance(ep, (list, tuple)) and len(ep) == 2:
            sx, sy = float(sp[0]), float(sp[1])
            ex, ey = float(ep[0]), float(ep[1])
        else:
            sx = sy = ex = ey = None
        return {
            "type": "drag",
            "coord_space": "screenshot_px",
            "start": {"x": sx, "y": sy},
            "end": {"x": ex, "y": ey},
        }

    if t == "scroll":
        pt = p.get("point")
        direction = str(p.get("direction") or "").strip().lower()
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            sx, sy = float(pt[0]), float(pt[1])
        else:
            sx, sy = None, None

        dx = dy = 0.0
        if direction in {"up", "down"}:
            mag = float(scroll_frac) * float(render_h or 0)
            dy = +mag if direction == "up" else -mag
        elif direction in {"left", "right"}:
            mag = float(scroll_frac) * float(render_w or 0)
            dx = +mag if direction == "left" else -mag

        ex = (sx + dx) if sx is not None else None
        ey = (sy + dy) if sy is not None else None
        return {
            "type": "scroll",
            "coord_space": "screenshot_px",
            "start": {"x": sx, "y": sy},
            "end": {"x": ex, "y": ey},
            "direction": direction or None,
            "scroll_frac": float(scroll_frac),
        }

    if t == "type":
        return {"type": "type", "content": p.get("content", "")}

    if t == "open_app":
        return {"type": "open_app", "app_name": p.get("app_name", "")}

    if t == "press_back":
        return {"type": "press_back"}

    if t == "press_home":
        return {"type": "home"}

    if t == "hotkey":
        key = str(p.get("key") or "").strip().lower()
        if key in {"enter", "return", "search", "go"}:
            return {"type": "type", "text": "", "key": "enter"}
        if key in {"back", "esc"}:
            return {"type": "press_back"}
        if key in {"home", "meta"}:
            return {"type": "home"}
        return {"type": "unknown", "hotkey": key}

    if t == "wait":
        return {"type": "wait", "duration_ms": 1000}

    if t == "finished":
        return {"type": "finished", "content": p.get("content", "")}

    return {"type": "unknown", "raw_type": t, "params": p}


class UiTars7BAdapter:
    def run_case(self, *, case_dir: Path, evidence_dir: Path, ctx) -> Dict[str, Any]:
        parse_mobile_output, prompt_template = _try_import_uitars_kit()
        cfg = UiTarsConfig.from_env()
        verbose = _env_bool("MAS_UITARS_VERBOSE", False)
        trace_coords = _env_bool("MAS_UITARS_TRACE_COORDS", False)
        post_action_sleep_s = _env_float("MAS_UITARS_POST_ACTION_SLEEP_S", 1.5)

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

        case_id = str(task["task_id"])
        agent_id = str(ctx.registry_entry.get("agent_id") or "")

        writer = EvidenceWriter(
            run_dir=ctx.output_dir,
            case_id=case_id,
            seed=ctx.seed,
            run_mode="phase3",
            metadata={
                **(ctx.run_metadata or {}),
                "agent_id": agent_id,
                "adapter": "ui_tars_7b",
            },
            episode_dir=evidence_dir,
        )

        env: Any
        has_android = bool(getattr(ctx.phase0_cfg, "android_serial", None))
        if has_android:
            env = AndroidEnvAdapter(
                adb_path=str(getattr(ctx.phase0_cfg, "adb_path", "adb")),
                serial=str(getattr(ctx.phase0_cfg, "android_serial")),
                evidence_writer=writer,
                action_trace_level=getattr(ctx.phase0_cfg, "action_trace_level", None),
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
        agent_failed = False
        agent_failed_reason: str | None = None
        error: Dict[str, Any] | None = None
        decision: Dict[str, Any] = {
            "success": False,
            "conclusive": False,
            "score": 0.0,
            "reason": "",
        }

        history: list[tuple[str, str]] = []

        extra_headers: dict[str, str] = {}
        site_url = os.environ.get("OPENROUTER_SITE_URL")
        site_name = os.environ.get("OPENROUTER_SITE_NAME")
        if site_url:
            extra_headers["HTTP-Referer"] = site_url
        if site_name:
            extra_headers["X-Title"] = site_name

        if verbose:
            print(
                _safe_json(
                    {
                        "event": "ui_tars_adapter_start",
                        "agent_id": agent_id,
                        "case_id": case_id,
                        "provider": cfg.provider if has_android else None,
                        "model_id": cfg.model_id if has_android else None,
                        "base_url": cfg.base_url if has_android else None,
                        "history_n": int(cfg.history_n),
                        "trace_coords": bool(trace_coords),
                        "post_action_sleep_s": float(post_action_sleep_s),
                    }
                )
            )
            if has_android and cfg.provider == "openrouter" and not cfg.api_key:
                print(
                    _safe_json(
                        {
                            "event": "ui_tars_adapter_warning",
                            "warning": "missing_openrouter_api_key",
                            "hint": "export OPENROUTER_API_KEY (or OPEN_ROUTER_API_KEY) before running",
                        }
                    )
                )

        try:
            reset_event = reset_for_episode(
                controller=env,
                initial_state=task.get("initial_state"),
                reset_strategy=ctx.phase0_cfg.reset_strategy,
                snapshot_tag=ctx.phase0_cfg.snapshot_tag,
            )
            writer.record_reset(reset_event)

            episode_time, episode_time_event = capture_episode_time(controller=env, task_spec=task)
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

            user_goal = str(task.get("user_goal", ""))
            policy_digest = stable_sha256(policy)

            max_steps_task = int(task.get("max_steps", 20))
            max_steps_policy = int(policy.get("budgets", {}).get("max_steps", max_steps_task))
            max_steps = min(max_steps_task, max_steps_policy)

            terminated_reason = "max_steps"
            for step in range(max_steps):
                if verbose:
                    print(f"\n===== UI-TARS STEP {step + 1} / {max_steps} =====")
                try:
                    obs = env.observe(step=step, dump_ui=(step == 0 or step % 5 == 0))
                except TypeError:
                    obs = env.observe()

                writer.record_observation(step=step, observation=obs)
                set_current_obs_digest = getattr(env, "set_current_obs_digest", None)
                if callable(set_current_obs_digest):
                    set_current_obs_digest(writer.last_obs_digest)

                screenshot_png = obs.get("screenshot_png") if isinstance(obs, dict) else None
                screenshot_png_bytes = screenshot_png if isinstance(screenshot_png, (bytes, bytearray)) else b""
                screenshot_sha256 = stable_sha256(screenshot_png_bytes)
                if verbose:
                    render_w, render_h = _png_size_px(screenshot_png_bytes)
                    print(
                        _safe_json(
                            {
                                "event": "ui_tars_observation",
                                "step_idx": step,
                                "screenshot_size_px": (render_w, render_h),
                                "screenshot_sha256": screenshot_sha256,
                                "ui_hash": obs.get("ui_hash") if isinstance(obs, dict) else None,
                            }
                        )
                    )
                input_digest = stable_sha256(
                    {
                        "case_id": case_id,
                        "step_idx": step,
                        "user_goal": user_goal,
                        "policy_digest": policy_digest,
                        "ui_hash": obs.get("ui_hash") if isinstance(obs, dict) else None,
                        "screenshot_sha256": screenshot_sha256,
                    }
                )

                if not has_android:
                    raw_action: dict[str, Any] = {
                        "type": "finished" if step == 0 else "wait",
                        "ui_tars": {"mode": "no_android_serial"},
                    }
                    response_digest = stable_sha256(raw_action)
                    writer.record_agent_call(
                        {
                            "step_idx": step,
                            "agent_name": "ui-tars-7b",
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
                    normalized_action = writer.record_agent_action(step=step, action=raw_action)
                else:
                    screenshot_b64 = base64.b64encode(screenshot_png_bytes).decode("utf-8")
                    history_trimmed = history[-max(0, int(cfg.history_n)) :] if cfg.history_n > 0 else []
                    messages = _build_messages(
                        prompt_template=prompt_template,
                        instruction=user_goal,
                        language=cfg.language,
                        screenshot_b64=screenshot_b64,
                        history=history_trimmed,
                    )

                    try:
                        text, tokens_in, tokens_out, latency_ms = _call_model_with_retries(
                            cfg=cfg, messages=messages, extra_headers=extra_headers
                        )
                        response_digest = stable_sha256(text)
                        writer.record_agent_call(
                            {
                                "step_idx": step,
                                "agent_name": "ui-tars-7b",
                                "provider": cfg.provider,
                                "model_id": cfg.model_id,
                                "base_url": cfg.base_url,
                                "input_digest": input_digest,
                                "response_digest": response_digest,
                                "latency_ms": latency_ms,
                                "tokens_in": tokens_in,
                                "tokens_out": tokens_out,
                                "error": None,
                            }
                        )
                        if verbose:
                            print(
                                _safe_json(
                                    {
                                        "event": "ui_tars_model_call",
                                        "step_idx": step,
                                        "provider": cfg.provider,
                                        "model_id": cfg.model_id,
                                        "base_url": cfg.base_url,
                                        "latency_ms": latency_ms,
                                        "tokens_in": tokens_in,
                                        "tokens_out": tokens_out,
                                        "input_digest": input_digest,
                                        "response_digest": response_digest,
                                    }
                                )
                            )
                            print("\n[MODEL OUTPUT]\n" + str(text))
                    except Exception as e:
                        latency_ms = 0
                        writer.record_agent_call(
                            {
                                "step_idx": step,
                                "agent_name": "ui-tars-7b",
                                "provider": cfg.provider,
                                "model_id": cfg.model_id,
                                "base_url": cfg.base_url,
                                "input_digest": input_digest,
                                "response_digest": None,
                                "latency_ms": latency_ms,
                                "tokens_in": None,
                                "tokens_out": None,
                                "error": {"type": type(e).__name__, "repr": repr(e)},
                            }
                        )
                        agent_failed = True
                        agent_failed_reason = f"model_error:{type(e).__name__}"
                        terminated_reason = "agent_failed"
                        raw_action = {"type": "unknown", "ui_tars": {"error": agent_failed_reason}}
                        normalized_action = writer.record_agent_action(step=step, action=raw_action)
                        if verbose:
                            print(
                                _safe_json(
                                    {
                                        "event": "ui_tars_model_error",
                                        "step_idx": step,
                                        "error": {"type": type(e).__name__, "repr": repr(e)},
                                    }
                                )
                            )

                    if not agent_failed:
                        try:
                            parsed = parse_mobile_output(text)
                            parsed_thought = getattr(parsed, "thought", "")
                            parsed_raw_action = getattr(parsed, "raw_action", "")
                            parsed_actions = getattr(parsed, "actions", []) or []
                            first = parsed_actions[0] if parsed_actions else None
                            act_type = getattr(first, "type", "unknown")
                            params = getattr(first, "params", {}) if first is not None else {}
                            render_w, render_h = _png_size_px(screenshot_png_bytes)
                            mapped = _map_uitars_action(
                                action_type=str(act_type),
                                params=dict(params) if isinstance(params, dict) else {},
                                render_w=render_w,
                                render_h=render_h,
                                scroll_frac=cfg.scroll_frac,
                            )
                            raw_action = {
                                **mapped,
                                **({"trace_coords": True} if trace_coords else {}),
                                "ui_tars": {
                                    "thought": str(parsed_thought or ""),
                                    "raw_action": str(parsed_raw_action or ""),
                                    "model_output": str(text)[:4000],
                                },
                            }
                            if verbose:
                                print(
                                    _safe_json(
                                        {
                                            "event": "ui_tars_parsed",
                                            "step_idx": step,
                                            "thought": str(parsed_thought or ""),
                                            "raw_action": str(parsed_raw_action or ""),
                                            "action0": {"type": str(act_type), "params": params},
                                        }
                                    )
                                )
                                print(_safe_json({"event": "ui_tars_mapped_action", "step_idx": step, "action": mapped}))
                        except Exception as e:
                            agent_failed = True
                            agent_failed_reason = f"parse_error:{type(e).__name__}"
                            terminated_reason = "agent_failed"
                            raw_action = {"type": "unknown", "ui_tars": {"error": agent_failed_reason, "model_output": str(text)[:4000]}}
                            if verbose:
                                print(
                                    _safe_json(
                                        {
                                            "event": "ui_tars_parse_error",
                                            "step_idx": step,
                                            "error": {"type": type(e).__name__, "repr": repr(e)},
                                        }
                                    )
                                )

                        normalized_action = writer.record_agent_action(step=step, action=raw_action)
                        if verbose:
                            print(
                                _safe_json(
                                    {
                                        "event": "ui_tars_normalized_action",
                                        "step_idx": step,
                                        "action": normalized_action,
                                    }
                                )
                            )
                        if not agent_failed:
                            history.append((screenshot_b64, text))
                            history = history[-max(0, int(cfg.history_n)) :] if cfg.history_n > 0 else []

                if normalized_action.get("type") == "finished":
                    terminated_reason = "agent_stop"
                    break

                if agent_failed:
                    break

                result = env.execute(normalized_action)
                writer.record_action(step=step, action=normalized_action, result=result)
                steps_executed += 1
                if verbose:
                    print(_safe_json({"event": "ui_tars_execute_result", "step_idx": step, "result": result}))
                if post_action_sleep_s > 0.0 and str(normalized_action.get("type") or "") not in {"wait"}:
                    if verbose:
                        print(
                            _safe_json(
                                {
                                    "event": "ui_tars_post_action_sleep",
                                    "step_idx": step,
                                    "sleep_s": float(post_action_sleep_s),
                                }
                            )
                        )
                    time.sleep(float(post_action_sleep_s))

                if isinstance(result, dict):
                    if result.get("agent_failed") is True:
                        agent_failed = True
                        agent_failed_reason = str(result.get("error") or "agent_failed")
                        terminated_reason = "agent_failed"
                        break
                    if result.get("infra_failed") is True:
                        infra_analysis["infra_failed"] = True
                        infra_analysis.setdefault("infra_failure_reasons", [])
                        if isinstance(infra_analysis["infra_failure_reasons"], list):
                            infra_analysis["infra_failure_reasons"].append(
                                str(result.get("error") or "executor_infra_failed")
                            )
                        terminated_reason = "infra_failed"
                        break

            post_evidence = oracle.post_check(oracle_ctx)
            writer.record_oracle_events(post_evidence)
            decision = decision_from_evidence(post_evidence, oracle_id=getattr(oracle, "oracle_id", None))

        except Exception as e:
            terminated_reason = "runner_error"
            error = {"type": type(e).__name__, "repr": repr(e)}
            writer.record_device_event({"event": "runner_error", **error})

        success = bool(decision.get("success", False))
        conclusive = bool(decision.get("conclusive", False))
        score = float(decision.get("score", 0.0))
        reason = str(decision.get("reason", ""))

        if agent_failed:
            status = "inconclusive"
            failure_class = "agent_failed"
        elif infra_analysis.get("infra_failed") is True:
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
            "steps": steps_executed,
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
                "runner": "ui_tars_7b",
                "infra": infra_analysis,
                "error": error,
                "agent_failed_reason": agent_failed_reason,
                "provider": cfg.provider if has_android else None,
                "model_id": cfg.model_id if has_android else None,
                "base_url": cfg.base_url if has_android else None,
            },
        }
        summary = writer.write_summary(summary)
        writer.close()
        return summary


def create_adapter() -> UiTars7BAdapter:
    return UiTars7BAdapter()
