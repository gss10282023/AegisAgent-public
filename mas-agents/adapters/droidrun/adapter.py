from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from dotenv import load_dotenv

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

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_ADAPTER_ROOT = Path(__file__).resolve().parent


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_repr(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool, dict, list)):
        return obj
    try:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # pydantic
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
    except Exception:
        pass
    return repr(obj)


def _result_field(result: Any, key: str) -> Any:
    if isinstance(result, Mapping):
        return result.get(key)
    return getattr(result, key, None)


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None
    return None


def _parse_macro_timestamp_ms(raw: Any) -> int | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d_%H%M"):
        try:
            dt = datetime.strptime(value, fmt)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue
    return None


def _list_dirs(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return [p for p in path.iterdir() if p.is_dir()]


def _pick_newest_dir(dirs: Iterable[Path]) -> Path | None:
    candidates = [d for d in dirs if d.exists() and d.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _detect_trace_dir(*, base_dir: Path, before: set[Path]) -> Path | None:
    after = set(_list_dirs(base_dir))
    created = after - set(before)
    if len(created) == 1:
        return next(iter(created))
    if created:
        return _pick_newest_dir(created)
    return _pick_newest_dir(after)


def _parse_bounds_csv(bounds: Any) -> list[int] | None:
    if not isinstance(bounds, str):
        return None
    parts = [p.strip() for p in bounds.split(",")]
    if len(parts) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(p) for p in parts)
    except Exception:
        return None
    if x2 < x1 or y2 < y1:
        return None
    return [x1, y1, x2, y2]


def _ui_state_to_a11y_tree(ui_state: Any) -> dict[str, Any] | None:
    if not isinstance(ui_state, list):
        return None

    nodes: list[dict[str, Any]] = []
    for el in ui_state:
        if not isinstance(el, Mapping):
            continue
        bbox = _parse_bounds_csv(el.get("bounds"))
        if bbox is None:
            continue

        resource_id = el.get("resourceId")
        resource_id_str = str(resource_id).strip() if isinstance(resource_id, str) else ""
        pkg = ""
        if ":" in resource_id_str:
            pkg = resource_id_str.split(":", 1)[0].strip()

        node: dict[str, Any] = {
            "bounds": bbox,
            "text": el.get("text"),
            "resource_id": resource_id_str or None,
            "class": el.get("className"),
            "package": pkg or None,
            "clickable": bool(el.get("clickable") is True),
            "id": str(el.get("index")) if el.get("index") is not None else None,
        }
        nodes.append(node)

    return {
        "source": "droidrun_ui_state_v1",
        "nodes": nodes,
        "nodes_count": len(nodes),
    }


def _sorted_indexed_files(dir_path: Path, *, suffix: str) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    if not dir_path.exists():
        return out
    for p in dir_path.iterdir():
        if not p.is_file() or p.suffix.lower() != suffix.lower():
            continue
        try:
            idx = int(p.stem)
        except Exception:
            continue
        out.append((idx, p))
    out.sort(key=lambda t: t[0])
    return out


def _observation_from_droidrun_artifacts(
    *,
    step: int,
    screenshots: Mapping[int, Path],
    ui_states: Mapping[int, Path],
) -> dict[str, Any]:
    observation: dict[str, Any] = {}

    screenshot_path = screenshots.get(step)
    if screenshot_path is not None and screenshot_path.exists():
        observation["screenshot_png"] = screenshot_path.read_bytes()

    ui_state_path = ui_states.get(step)
    if ui_state_path is not None and ui_state_path.exists():
        try:
            ui_state = json.loads(ui_state_path.read_text(encoding="utf-8"))
        except Exception:
            ui_state = None
        a11y_tree = _ui_state_to_a11y_tree(ui_state)
        if a11y_tree is not None:
            observation["a11y_tree"] = a11y_tree

    return observation


def _write_agent_events_v1(
    *,
    path: Path,
    macro: Mapping[str, Any] | None,
    trace_dir: Path | None,
    fallback_note: str | None = None,
) -> list[dict[str, Any]]:
    base_ts_ms = _parse_macro_timestamp_ms(macro.get("timestamp") if macro else None)
    if base_ts_ms is None:
        base_ts_ms = _utc_ms()

    actions = []
    if macro is not None:
        raw_actions = macro.get("actions")
        if isinstance(raw_actions, list):
            actions = [a for a in raw_actions if isinstance(a, Mapping)]

    events: list[dict[str, Any]] = []
    for i, action in enumerate(actions):
        action_type = str(action.get("action_type") or "").strip().lower()
        if not action_type:
            action_type = str(action.get("type") or "").strip().lower()

        event: dict[str, Any] = {
            "timestamp_ms": int(base_ts_ms + (i * 1000)),
            "ref_step_idx": int(i),
            "droidrun_trace_dir": str(trace_dir) if trace_dir is not None else None,
            "raw_event": dict(action),
        }

        if action_type in {"start_app", "open_app", "startapp"}:
            event["type"] = "open_app"
            pkg = action.get("package")
            if isinstance(pkg, str) and pkg.strip():
                event["package"] = pkg.strip()
            activity = action.get("activity")
            if isinstance(activity, str) and activity.strip():
                event["activity"] = activity.strip()
        elif action_type == "tap":
            event["type"] = "tap"
            event["coord_space"] = "physical_px"
            event["x"] = action.get("x")
            event["y"] = action.get("y")
        elif action_type == "swipe":
            event["type"] = "swipe"
            event["coord_space"] = "physical_px"
            event["start"] = {"x": action.get("start_x"), "y": action.get("start_y")}
            event["end"] = {"x": action.get("end_x"), "y": action.get("end_y")}
            if "duration_ms" in action:
                event["duration_ms"] = action.get("duration_ms")
        elif action_type in {"long_press", "longpress"}:
            event["type"] = "long_press"
            event["coord_space"] = "physical_px"
            event["x"] = action.get("x")
            event["y"] = action.get("y")
            if "duration_ms" in action:
                event["duration_ms"] = action.get("duration_ms")
        elif action_type == "type":
            event["type"] = "type"
            event["text"] = action.get("text")
        elif action_type in {"back", "press_back"}:
            event["type"] = "back"
        elif action_type == "home":
            event["type"] = "home"
        else:
            event["type"] = "wait"
            event["mapping_note"] = f"unsupported_macro_action_type:{action_type or 'unknown'}"

        events.append(event)

    # Ensure a non-empty stream so Phase3 finalizer doesn't degrade L1 to none.
    events.append(
        {
            "timestamp_ms": int(base_ts_ms + (len(events) * 1000)),
            "ref_step_idx": int(len(actions)),
            "type": "finished",
            "note": fallback_note,
            "droidrun_trace_dir": str(trace_dir) if trace_dir is not None else None,
        }
    )

    path.write_text("", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(_safe_json(e))
            f.write("\n")
    return events


@dataclass(frozen=True)
class _StepLLMUsage:
    tokens_in: int | None
    tokens_out: int | None


def _extract_executor_usage_by_step(trajectory: Any) -> list[_StepLLMUsage]:
    if not isinstance(trajectory, list):
        return []
    out: list[_StepLLMUsage] = []
    for e in trajectory:
        if not isinstance(e, Mapping):
            continue
        if e.get("type") != "ExecutorResponseEvent":
            continue
        usage = e.get("usage")
        if not isinstance(usage, Mapping):
            out.append(_StepLLMUsage(tokens_in=None, tokens_out=None))
            continue
        req = usage.get("request_tokens")
        resp = usage.get("response_tokens")
        tokens_in = int(req) if isinstance(req, int) else None
        tokens_out = int(resp) if isinstance(resp, int) else None
        out.append(
            _StepLLMUsage(
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        )
    return out


def _extract_executor_action_results(trajectory: Any) -> list[dict[str, Any]]:
    if not isinstance(trajectory, list):
        return []
    out: list[dict[str, Any]] = []
    for e in trajectory:
        if isinstance(e, Mapping) and e.get("type") == "ExecutorActionResultEvent":
            out.append(dict(e))
    return out


def _record_noop_step(
    *,
    writer: EvidenceWriter,
    step: int,
    agent_id: str,
    provider: str | None,
    model_id: str | None,
    base_url: str | None,
    note: str,
    action_type: str = "finished",
) -> None:
    if action_type == "wait":
        action: Dict[str, Any] = {"type": "wait", "duration_ms": 0, "note": str(note)}
    else:
        action = {"type": "finished", "note": str(note)}

    input_digest = stable_sha256(
        {
            "step": int(step),
            "note": str(note),
            "obs_digest": writer.last_obs_digest,
        }
    )
    response_digest = stable_sha256(action)

    writer.record_agent_call(
        {
            "step_idx": int(step),
            "agent_name": agent_id,
            "provider": provider,
            "model_id": model_id,
            "base_url": base_url,
            "input_digest": input_digest,
            "response_digest": response_digest,
            "latency_ms": None,
            "tokens_in": None,
            "tokens_out": None,
            "error": None,
        }
    )
    normalized = writer.record_agent_action(step=int(step), action=action)
    writer.record_action(
        step=int(step),
        action=normalized,
        result={"ok": True, "dry_run": True, "note": str(note)},
    )


def _action_from_macro(action: Mapping[str, Any]) -> dict[str, Any]:
    action_type = str(action.get("action_type") or "").strip().lower()
    if not action_type:
        action_type = str(action.get("type") or "").strip().lower()

    if action_type in {"start_app", "open_app", "startapp"}:
        out: dict[str, Any] = {
            "type": "open_app",
            "package": action.get("package"),
        }
        if "activity" in action:
            out["activity"] = action.get("activity")
        return out

    if action_type == "tap":
        return {
            "type": "tap",
            "coord_space": "physical_px",
            "x": action.get("x"),
            "y": action.get("y"),
            "element_index": action.get("element_index"),
            "element_text": action.get("element_text"),
            "element_bounds": action.get("element_bounds"),
        }

    if action_type == "swipe":
        out = {
            "type": "swipe",
            "coord_space": "physical_px",
            "start": {"x": action.get("start_x"), "y": action.get("start_y")},
            "end": {"x": action.get("end_x"), "y": action.get("end_y")},
        }
        if "duration_ms" in action:
            out["duration_ms"] = action.get("duration_ms")
        return out

    if action_type in {"long_press", "longpress"}:
        out = {
            "type": "long_press",
            "coord_space": "physical_px",
            "x": action.get("x"),
            "y": action.get("y"),
        }
        if "duration_ms" in action:
            out["duration_ms"] = action.get("duration_ms")
        return out

    if action_type == "type":
        return {"type": "type", "text": action.get("text")}

    if action_type in {"back", "press_back"}:
        return {"type": "press_back"}

    if action_type == "home":
        return {"type": "home"}

    return {"type": "wait", "duration_ms": 0, "raw": dict(action)}


async def _run_droidrun(
    *,
    goal: str,
    max_steps: int,
    android_serial: str,
    trace_base_dir: Path,
    openrouter_api_key: str,
) -> Any:
    from droidrun import (
        AgentConfig,
        DeviceConfig,
        DroidAgent,
        DroidrunConfig,
        ExecutorConfig,
        LoggingConfig,
        ManagerConfig,
    )
    from llama_index.llms.openai import OpenAI
    from llama_index.llms.openai_like import OpenAILike

    config = DroidrunConfig(
        agent=AgentConfig(
            max_steps=int(max_steps),
            reasoning=True,
            after_sleep_action=1.0,
            wait_for_stable_ui=0.3,
            manager=ManagerConfig(vision=True),
            executor=ExecutorConfig(vision=True),
        ),
        logging=LoggingConfig(
            debug=True,
            save_trajectory="action",
            trajectory_path=str(trace_base_dir),
            trajectory_gifs=False,
            rich_text=False,
        ),
        device=DeviceConfig(
            serial=str(android_serial),
            platform="android",
            use_tcp=False,
        ),
    )

    agent = DroidAgent(
        goal=str(goal),
        config=config,
        llms={
            "manager": OpenAI(
                model="gpt-5.1",
                api_base=OPENROUTER_BASE_URL,
                api_key=openrouter_api_key,
            ),
            "executor": OpenAILike(
                model="google/gemini-3-flash-preview",
                api_base=OPENROUTER_BASE_URL,
                api_key=openrouter_api_key,
                is_chat_model=True,
            ),
            "codeact": OpenAI(
                model="gpt-5-mini",
                api_base=OPENROUTER_BASE_URL,
                api_key=openrouter_api_key,
            ),
            "text_manipulator": OpenAI(
                model="gpt-5-mini",
                api_base=OPENROUTER_BASE_URL,
                api_key=openrouter_api_key,
            ),
            "app_opener": OpenAI(
                model="gpt-5-mini",
                api_base=OPENROUTER_BASE_URL,
                api_key=openrouter_api_key,
            ),
            "scripter": OpenAI(
                model="gpt-5-mini",
                api_base=OPENROUTER_BASE_URL,
                api_key=openrouter_api_key,
            ),
            "structured_output": OpenAI(
                model="gpt-5-mini",
                api_base=OPENROUTER_BASE_URL,
                api_key=openrouter_api_key,
            ),
        },
    )
    return await agent.run()


class DroidrunAdapter:
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
        agent_id = str(ctx.registry_entry.get("agent_id") or "droidrun")

        writer = EvidenceWriter(
            run_dir=ctx.output_dir,
            case_id=case_id,
            seed=ctx.seed,
            run_mode="phase3",
            metadata={
                **(ctx.run_metadata or {}),
                "agent_id": agent_id,
                "adapter": "droidrun",
                "phase0": {
                    "execution_mode": getattr(ctx.phase0_cfg, "execution_mode", None),
                    "agent_name": getattr(ctx.phase0_cfg, "agent_name", None),
                },
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

        terminated_reason = "unknown"
        steps_executed = 0
        error: Any = None
        infra_analysis: Dict[str, Any] = {"infra_failed": False, "infra_failure_reasons": []}
        decision: Dict[str, Any] = {
            "success": False,
            "conclusive": False,
            "score": 0.0,
            "reason": "not_run",
        }
        episode_time = None
        oracle_ctx = None

        agent_events_path_run = ctx.output_dir / "agent_events.jsonl"
        agent_events_path_evidence = evidence_dir / "agent_events.jsonl"

        droidrun_trace_dir: Path | None = None
        droidrun_result: Any = None
        macro: dict[str, Any] | None = None
        trajectory: Any = None

        try:
            reset_event = reset_for_episode(
                controller=env,
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
            max_steps = int(task.get("max_steps", 15))

            if not has_android:
                terminated_reason = "dry_run_no_android"
                env.reset()
                obs = env.observe()
                writer.record_observation(step=0, observation=obs)
                _write_agent_events_v1(
                    path=agent_events_path_run,
                    macro=None,
                    trace_dir=None,
                    fallback_note="dry_run_no_android",
                )
                agent_events_path_evidence.write_text(
                    agent_events_path_run.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

                action = {"type": "wait", "duration_ms": 0, "note": "dry_run_no_android"}
                input_digest = stable_sha256(
                    {
                        "step": 0,
                        "ui_hash": obs.get("ui_hash"),
                        "obs_digest": writer.last_obs_digest,
                    }
                )
                response_digest = stable_sha256(action)
                writer.record_agent_call(
                    {
                        "step_idx": 0,
                        "agent_name": agent_id,
                        "provider": "openrouter",
                        "model_id": "google/gemini-3-flash-preview",
                        "base_url": OPENROUTER_BASE_URL,
                        "input_digest": input_digest,
                        "response_digest": response_digest,
                        "latency_ms": None,
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
                steps_executed = 1
            else:
                load_dotenv()
                openrouter_api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
                if not openrouter_api_key:
                    raise RuntimeError(
                        "Missing API key. Set `OPENROUTER_API_KEY` (recommended) or "
                        "`OPENAI_API_KEY`."
                    )

                trace_base_dir = evidence_dir / "droidrun_traces"
                trace_base_dir.mkdir(parents=True, exist_ok=True)
                before_dirs = set(_list_dirs(trace_base_dir))

                droidrun_result = asyncio.run(
                    _run_droidrun(
                        goal=user_goal,
                        max_steps=max_steps,
                        android_serial=str(getattr(ctx.phase0_cfg, "android_serial")),
                        trace_base_dir=trace_base_dir,
                        openrouter_api_key=openrouter_api_key,
                    )
                )
                terminated_reason = "agent_completed"
                if _result_field(droidrun_result, "success") is True:
                    terminated_reason = "agent_stop"
                else:
                    result_steps = _coerce_int(_result_field(droidrun_result, "steps"))
                    if result_steps is not None and result_steps >= int(max_steps):
                        terminated_reason = "max_steps"

                droidrun_trace_dir = _detect_trace_dir(base_dir=trace_base_dir, before=before_dirs)

                if droidrun_trace_dir is not None:
                    macro_path = droidrun_trace_dir / "macro.json"
                    if macro_path.exists():
                        try:
                            macro = json.loads(macro_path.read_text(encoding="utf-8"))
                        except Exception:
                            macro = None
                    traj_path = droidrun_trace_dir / "trajectory.json"
                    if traj_path.exists():
                        try:
                            trajectory = json.loads(traj_path.read_text(encoding="utf-8"))
                        except Exception:
                            trajectory = None

                fallback_note: str | None = None
                if droidrun_trace_dir is None:
                    fallback_note = "missing_droidrun_trace_dir"
                elif macro is None:
                    fallback_note = "missing_macro_json"

                events = _write_agent_events_v1(
                    path=agent_events_path_run,
                    macro=macro,
                    trace_dir=droidrun_trace_dir,
                    fallback_note=fallback_note,
                )
                agent_events_path_evidence.write_text(
                    agent_events_path_run.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )

                macro_actions: list[Mapping[str, Any]] = []
                if macro is not None and isinstance(macro.get("actions"), list):
                    macro_actions = [a for a in macro["actions"] if isinstance(a, Mapping)]

                usage_by_step = _extract_executor_usage_by_step(trajectory)
                results_by_step = _extract_executor_action_results(trajectory)

                if droidrun_trace_dir is None:
                    screenshots: dict[int, Path] = {}
                    ui_states: dict[int, Path] = {}
                else:
                    screenshots = dict(
                        _sorted_indexed_files(droidrun_trace_dir / "screenshots", suffix=".png")
                    )
                    ui_states = dict(
                        _sorted_indexed_files(droidrun_trace_dir / "ui_states", suffix=".json")
                    )

                if not macro_actions:
                    writer.record_observation(
                        step=0,
                        observation=_observation_from_droidrun_artifacts(
                            step=0,
                            screenshots=screenshots,
                            ui_states=ui_states,
                        ),
                    )
                    _record_noop_step(
                        writer=writer,
                        step=0,
                        agent_id=agent_id,
                        provider="openrouter",
                        model_id="google/gemini-3-flash-preview",
                        base_url=OPENROUTER_BASE_URL,
                        note=fallback_note or "no_macro_actions",
                        action_type="wait",
                    )
                    steps_executed = 1

                for step, macro_action in enumerate(macro_actions):
                    if step >= max_steps:
                        break
                    writer.record_observation(
                        step=int(step),
                        observation=_observation_from_droidrun_artifacts(
                            step=int(step),
                            screenshots=screenshots,
                            ui_states=ui_states,
                        ),
                    )
                    raw_action = _action_from_macro(macro_action)
                    raw_action["droidrun"] = {
                        "trace_dir": str(droidrun_trace_dir) if droidrun_trace_dir else None,
                        "macro_action": dict(macro_action),
                        "agent_events_v1": events[step] if step < len(events) else None,
                    }

                    if step < len(usage_by_step):
                        usage = usage_by_step[step]
                    else:
                        usage = _StepLLMUsage(tokens_in=None, tokens_out=None)

                    exec_result = results_by_step[step] if step < len(results_by_step) else None

                    input_digest = stable_sha256(
                        {
                            "step": int(step),
                            "obs_digest": writer.last_obs_digest,
                            "case_id": case_id,
                        }
                    )
                    response_digest = stable_sha256(raw_action)
                    writer.record_agent_call(
                        {
                            "step_idx": int(step),
                            "agent_name": agent_id,
                            "provider": "openrouter",
                            "model_id": "google/gemini-3-flash-preview",
                            "base_url": OPENROUTER_BASE_URL,
                            "input_digest": input_digest,
                            "response_digest": response_digest,
                            "latency_ms": None,
                            "tokens_in": usage.tokens_in,
                            "tokens_out": usage.tokens_out,
                            "error": None,
                        }
                    )
                    normalized = writer.record_agent_action(step=int(step), action=raw_action)
                    if isinstance(exec_result, Mapping):
                        ok = bool(exec_result.get("success"))
                        err = exec_result.get("error")
                        summary = exec_result.get("summary")
                    else:
                        ok = None
                        err = None
                        summary = None
                    writer.record_action(
                        step=int(step),
                        action=normalized,
                        result={
                            "ok": ok,
                            "error": err if err not in ("", None) else None,
                            "summary": summary,
                            "raw_result": exec_result,
                        },
                    )
                    steps_executed = int(step) + 1

                final_obs = _observation_from_droidrun_artifacts(
                    step=int(steps_executed),
                    screenshots=screenshots,
                    ui_states=ui_states,
                )
                if final_obs:
                    writer.record_observation(step=int(steps_executed), observation=final_obs)
                    _record_noop_step(
                        writer=writer,
                        step=int(steps_executed),
                        agent_id=agent_id,
                        provider="openrouter",
                        model_id="google/gemini-3-flash-preview",
                        base_url=OPENROUTER_BASE_URL,
                        note="final_state_capture",
                        action_type="finished",
                    )

            if oracle_ctx is not None:
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
                "droidrun_trace_dir": str(droidrun_trace_dir) if droidrun_trace_dir else None,
                "droidrun_result": _safe_repr(droidrun_result),
                "agent_events_path_run": str(agent_events_path_run),
                "agent_events_path_evidence": str(agent_events_path_evidence),
                "error": error,
            },
        }
        summary = writer.write_summary(summary)
        writer.close()
        return summary


def create_adapter() -> DroidrunAdapter:
    return DroidrunAdapter()
