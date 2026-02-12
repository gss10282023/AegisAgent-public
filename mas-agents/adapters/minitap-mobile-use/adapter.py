from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from mas_harness.evidence import EvidenceWriter, stable_sha256
from mas_harness.examples import ToyEnv
from mas_harness.integration.agents.base import AgentAdapterError
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
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _sanitize_task_name(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "task"
    out: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("_")
    name = "".join(out).strip("_")
    return name[:80] if name else "task"


@dataclass
class _L1Recorder:
    writer: EvidenceWriter
    env: AndroidEnvAdapter
    agent_events_path: Path
    agent_id: str
    provider: str | None
    model_id: str | None
    base_url: str | None
    step: int = 0

    def __post_init__(self) -> None:
        self.agent_events_path.parent.mkdir(parents=True, exist_ok=True)
        self.agent_events_path.write_text("", encoding="utf-8")

    def _append_agent_event(self, event: Dict[str, Any]) -> None:
        # Best-effort validation via stable JSON encoding; agent_events_v1 parser tolerates extra keys.
        with self.agent_events_path.open("a", encoding="utf-8") as f:
            f.write(_safe_json(event))
            f.write("\n")

    def _record_step_observation(self, step: int) -> Dict[str, Any]:
        obs = self.env.observe(step=step, dump_ui=True)
        self.writer.record_observation(step=step, observation=obs)
        return obs

    async def record_tap(
        self,
        *,
        x: int,
        y: int,
        long_press: bool,
        long_press_duration_ms: int,
        exec_fn,
    ):
        step = int(self.step)
        obs = self._record_step_observation(step)

        action: Dict[str, Any] = {
            "type": "long_press" if long_press else "tap",
            "coord_space": "physical_px",
            "x": int(x),
            "y": int(y),
        }
        if long_press:
            action["duration_ms"] = int(max(0, long_press_duration_ms))

        input_digest = stable_sha256({"step": step, "ui_hash": obs.get("ui_hash"), "obs_digest": self.writer.last_obs_digest})
        response_digest = stable_sha256(action)
        self.writer.record_agent_call(
            {
                "step_idx": step,
                "agent_name": self.agent_id,
                "provider": self.provider,
                "model_id": self.model_id,
                "base_url": self.base_url,
                "input_digest": input_digest,
                "response_digest": response_digest,
                "latency_ms": None,
                "tokens_in": None,
                "tokens_out": None,
                "error": None,
            }
        )

        normalized = self.writer.record_agent_action(step=step, action=action)

        ok = False
        err: str | None = None
        result_obj: Any = None
        try:
            result_obj = await exec_fn()
            ok = getattr(result_obj, "error", None) in (None, "")
            err = getattr(result_obj, "error", None) if not ok else None
            return result_obj
        except Exception as e:
            err = repr(e)
            raise
        finally:
            self.writer.record_action(
                step=step,
                action=normalized,
                result={"ok": bool(ok), "error": err, "raw_result": _safe_repr(result_obj)},
            )
            event: Dict[str, Any] = {
                "timestamp_ms": _utc_ms(),
                "type": "long_press" if long_press else "tap",
                "coord_space": "physical_px",
                "x": int(x),
                "y": int(y),
                "ok": bool(ok),
                "error": err,
            }
            if long_press:
                event["duration_ms"] = int(max(0, long_press_duration_ms))
            self._append_agent_event(event)
            self.step += 1

    async def record_swipe(
        self,
        *,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int,
        exec_fn,
    ):
        step = int(self.step)
        obs = self._record_step_observation(step)

        action: Dict[str, Any] = {
            "type": "swipe",
            "coord_space": "physical_px",
            "start": {"x": int(start_x), "y": int(start_y)},
            "end": {"x": int(end_x), "y": int(end_y)},
            "duration_ms": int(max(0, duration_ms)),
        }

        input_digest = stable_sha256({"step": step, "ui_hash": obs.get("ui_hash"), "obs_digest": self.writer.last_obs_digest})
        response_digest = stable_sha256(action)
        self.writer.record_agent_call(
            {
                "step_idx": step,
                "agent_name": self.agent_id,
                "provider": self.provider,
                "model_id": self.model_id,
                "base_url": self.base_url,
                "input_digest": input_digest,
                "response_digest": response_digest,
                "latency_ms": None,
                "tokens_in": None,
                "tokens_out": None,
                "error": None,
            }
        )

        normalized = self.writer.record_agent_action(step=step, action=action)

        ok = False
        err: str | None = None
        result_obj: Any = None
        try:
            result_obj = await exec_fn()
            ok = result_obj in (None, "")
            err = str(result_obj) if not ok and result_obj is not None else None
            return result_obj
        except Exception as e:
            err = repr(e)
            raise
        finally:
            self.writer.record_action(
                step=step,
                action=normalized,
                result={"ok": bool(ok), "error": err, "raw_result": _safe_repr(result_obj)},
            )
            self._append_agent_event(
                {
                    "timestamp_ms": _utc_ms(),
                    "type": "swipe",
                    "coord_space": "physical_px",
                    "start": {"x": int(start_x), "y": int(start_y)},
                    "end": {"x": int(end_x), "y": int(end_y)},
                    "duration_ms": int(max(0, duration_ms)),
                    "ok": bool(ok),
                    "error": err,
                }
            )
            self.step += 1

    async def record_type(
        self,
        *,
        text: str,
        exec_fn,
    ):
        step = int(self.step)
        obs = self._record_step_observation(step)

        action: Dict[str, Any] = {"type": "type", "text": str(text)}

        input_digest = stable_sha256({"step": step, "ui_hash": obs.get("ui_hash"), "obs_digest": self.writer.last_obs_digest})
        response_digest = stable_sha256(action)
        self.writer.record_agent_call(
            {
                "step_idx": step,
                "agent_name": self.agent_id,
                "provider": self.provider,
                "model_id": self.model_id,
                "base_url": self.base_url,
                "input_digest": input_digest,
                "response_digest": response_digest,
                "latency_ms": None,
                "tokens_in": None,
                "tokens_out": None,
                "error": None,
            }
        )
        normalized = self.writer.record_agent_action(step=step, action=action)

        ok = False
        err: str | None = None
        result_obj: Any = None
        try:
            result_obj = await exec_fn()
            ok = bool(result_obj) is True
            err = None if ok else "type_failed"
            return result_obj
        except Exception as e:
            err = repr(e)
            raise
        finally:
            self.writer.record_action(
                step=step,
                action=normalized,
                result={"ok": bool(ok), "error": err, "raw_result": _safe_repr(result_obj)},
            )
            self._append_agent_event(
                {
                    "timestamp_ms": _utc_ms(),
                    "type": "type",
                    "text": str(text),
                    "ok": bool(ok),
                    "error": err,
                }
            )
            self.step += 1

    async def record_simple(
        self,
        *,
        event_type: str,
        action: Dict[str, Any],
        exec_fn,
    ):
        step = int(self.step)
        obs = self._record_step_observation(step)

        input_digest = stable_sha256({"step": step, "ui_hash": obs.get("ui_hash"), "obs_digest": self.writer.last_obs_digest})
        response_digest = stable_sha256(action)
        self.writer.record_agent_call(
            {
                "step_idx": step,
                "agent_name": self.agent_id,
                "provider": self.provider,
                "model_id": self.model_id,
                "base_url": self.base_url,
                "input_digest": input_digest,
                "response_digest": response_digest,
                "latency_ms": None,
                "tokens_in": None,
                "tokens_out": None,
                "error": None,
            }
        )
        normalized = self.writer.record_agent_action(step=step, action=action)

        ok = False
        err: str | None = None
        result_obj: Any = None
        try:
            result_obj = await exec_fn()
            if isinstance(result_obj, bool):
                ok = bool(result_obj)
                err = None if ok else f"{event_type}_failed"
            else:
                ok = result_obj in (None, "", 0)
                err = None if ok else str(result_obj)
            return result_obj
        except Exception as e:
            err = repr(e)
            raise
        finally:
            self.writer.record_action(
                step=step,
                action=normalized,
                result={"ok": bool(ok), "error": err, "raw_result": _safe_repr(result_obj)},
            )
            self._append_agent_event(
                {
                    "timestamp_ms": _utc_ms(),
                    "type": str(event_type),
                    "ok": bool(ok),
                    "error": err,
                    **_event_fields_for_action(action),
                }
            )
            self.step += 1


def _event_fields_for_action(action: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(action, dict):
        return {}
    out: Dict[str, Any] = {}
    if action.get("type") == "open_app":
        pkg = action.get("package")
        out["package"] = pkg if isinstance(pkg, str) else (str(pkg) if pkg is not None else None)
    if action.get("type") == "open_url":
        url = action.get("url")
        out["url"] = url if isinstance(url, str) else (str(url) if url is not None else None)
    return out


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


def _record_noop_step(
    *,
    writer: EvidenceWriter,
    step: int,
    agent_id: str,
    provider: str | None,
    model_id: str | None,
    base_url: str | None,
    note: str,
) -> None:
    action: Dict[str, Any] = {"type": "wait", "duration_ms": 0, "note": str(note)}
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
    writer.record_action(step=int(step), action=normalized, result={"ok": True, "dry_run": True, "note": str(note)})


class MinitapMobileUseAdapter:
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
        agent_id = str(ctx.registry_entry.get("agent_id") or "minitap-mobile-use")

        writer = EvidenceWriter(
            run_dir=ctx.output_dir,
            case_id=case_id,
            seed=ctx.seed,
            run_mode="phase3",
            metadata={
                **(ctx.run_metadata or {}),
                "agent_id": agent_id,
                "adapter": "minitap_mobile_use",
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
        decision: Dict[str, Any] = {"success": False, "conclusive": False, "score": 0.0, "reason": "not_run"}
        minitap_output: Any = None
        minitap_trace_dir: str | None = None
        agent_events_path = ctx.output_dir / "agent_events.jsonl"
        episode_time = None

        try:
            reset_event = reset_for_episode(
                controller=env,
                initial_state=task.get("initial_state"),
                reset_strategy=ctx.phase0_cfg.reset_strategy,
                snapshot_tag=ctx.phase0_cfg.snapshot_tag,
            )
            writer.record_reset(reset_event)

            if has_android:
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

            user_goal = str(task.get("user_goal") or "")
            max_steps = int(task.get("max_steps", 20))

            if not has_android:
                terminated_reason = "dry_run_no_android"
                obs = env.observe()
                writer.record_observation(step=0, observation=obs)
                _record_noop_step(
                    writer=writer,
                    step=0,
                    agent_id=agent_id,
                    provider=getattr(ctx.phase0_cfg, "agent_provider", None),
                    model_id=getattr(ctx.phase0_cfg, "agent_model_id", None),
                    base_url=getattr(ctx.phase0_cfg, "agent_base_url", None),
                    note="dry_run_no_android",
                )
                steps_executed = 1
            else:
                minitap_trace_base = evidence_dir / "minitap_traces"

                provider = getattr(ctx.phase0_cfg, "agent_provider", None)
                model_id = getattr(ctx.phase0_cfg, "agent_model_id", None)
                base_url = getattr(ctx.phase0_cfg, "agent_base_url", None)

                recorder = _L1Recorder(
                    writer=writer,
                    env=env,
                    agent_events_path=agent_events_path,
                    agent_id=agent_id,
                    provider=provider,
                    model_id=model_id,
                    base_url=base_url,
                )

                minitap_output, minitap_trace_dir = _run_minitap_with_recorder(
                    user_goal=user_goal,
                    task_name=_sanitize_task_name(case_id),
                    max_steps=max_steps,
                    android_serial=str(getattr(ctx.phase0_cfg, "android_serial")),
                    llm_config_path=_resolve_llm_config_path(ctx.repo_root),
                    trace_base_dir=minitap_trace_base,
                    recorder=recorder,
                )
                steps_executed = int(recorder.step)
                terminated_reason = "agent_completed"

                if steps_executed == 0:
                    obs = env.observe(step=0, dump_ui=True)
                    writer.record_observation(step=0, observation=obs)
                    _record_noop_step(
                        writer=writer,
                        step=0,
                        agent_id=agent_id,
                        provider=provider,
                        model_id=model_id,
                        base_url=base_url,
                        note="no_actions_captured",
                    )
                    steps_executed = 1

                recorder._append_agent_event({"timestamp_ms": _utc_ms(), "type": "finished"})

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
                "minitap_output": _safe_repr(minitap_output),
                "minitap_trace_dir": minitap_trace_dir,
                "agent_events_path": str(agent_events_path),
                "error": error,
            },
        }
        summary = writer.write_summary(summary)
        writer.close()
        return summary


def _resolve_llm_config_path(repo_root: Path) -> Path:
    override = _env_str("MAS_MINITAP_LLM_CONFIG", None)
    if override is not None:
        p = Path(override).expanduser()
        return p if p.is_absolute() else (repo_root / p)

    candidates = [
        _ADAPTER_ROOT / "llm-config.override.jsonc",
        repo_root / "llm-config.override.jsonc",
        _ADAPTER_ROOT / "backup" / "llm-config.override.jsonc",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise AgentAdapterError(
        "cannot find minitap LLM config override; set MAS_MINITAP_LLM_CONFIG or add llm-config.override.jsonc"
    )


def _run_minitap_with_recorder(
    *,
    user_goal: str,
    task_name: str,
    max_steps: int,
    android_serial: str,
    llm_config_path: Path,
    trace_base_dir: Path,
    recorder: _L1Recorder,
) -> tuple[Any, str | None]:
    async def _run() -> tuple[Any, str | None]:
        nonlocal recorder
        agent = None

        try:
            from minitap.mobile_use.context import DevicePlatform  # type: ignore
            from minitap.mobile_use.sdk import Agent  # type: ignore
            from minitap.mobile_use.sdk.types import AgentProfile  # type: ignore
            from minitap.mobile_use.sdk.builders import Builders  # type: ignore
            from minitap.mobile_use.controllers.android_controller import AndroidDeviceController  # type: ignore
        except Exception as e:
            raise AgentAdapterError(f"failed to import minitap SDK: {e}") from e

        orig_tap = AndroidDeviceController.tap
        orig_swipe = AndroidDeviceController.swipe
        orig_input_text = AndroidDeviceController.input_text
        orig_launch_app = AndroidDeviceController.launch_app
        orig_open_url = AndroidDeviceController.open_url
        orig_press_back = AndroidDeviceController.press_back
        orig_press_home = AndroidDeviceController.press_home
        orig_press_enter = AndroidDeviceController.press_enter

        async def tap_patched(self, coords, long_press: bool = False, long_press_duration: int = 1000):
            rec = recorder
            if rec is None:
                return await orig_tap(self, coords, long_press, long_press_duration)
            return await rec.record_tap(
                x=int(getattr(coords, "x")),
                y=int(getattr(coords, "y")),
                long_press=bool(long_press),
                long_press_duration_ms=int(long_press_duration),
                exec_fn=lambda: orig_tap(self, coords, long_press, long_press_duration),
            )

        async def swipe_patched(self, start, end, duration: int = 400):
            rec = recorder
            if rec is None:
                return await orig_swipe(self, start, end, duration)
            return await rec.record_swipe(
                start_x=int(getattr(start, "x")),
                start_y=int(getattr(start, "y")),
                end_x=int(getattr(end, "x")),
                end_y=int(getattr(end, "y")),
                duration_ms=int(duration),
                exec_fn=lambda: orig_swipe(self, start, end, duration),
            )

        async def input_text_patched(self, text: str):
            rec = recorder
            if rec is None:
                return await orig_input_text(self, text)
            return await rec.record_type(text=str(text), exec_fn=lambda: orig_input_text(self, text))

        async def launch_app_patched(self, package_or_bundle_id: str):
            rec = recorder
            if rec is None:
                return await orig_launch_app(self, package_or_bundle_id)
            return await rec.record_simple(
                event_type="open_app",
                action={"type": "open_app", "package": str(package_or_bundle_id)},
                exec_fn=lambda: orig_launch_app(self, package_or_bundle_id),
            )

        async def open_url_patched(self, url: str):
            rec = recorder
            if rec is None:
                return await orig_open_url(self, url)
            return await rec.record_simple(
                event_type="open_url",
                action={"type": "open_url", "url": str(url)},
                exec_fn=lambda: orig_open_url(self, url),
            )

        async def back_patched(self):
            rec = recorder
            if rec is None:
                return await orig_press_back(self)
            return await rec.record_simple(
                event_type="back",
                action={"type": "press_back"},
                exec_fn=lambda: orig_press_back(self),
            )

        async def home_patched(self):
            rec = recorder
            if rec is None:
                return await orig_press_home(self)
            return await rec.record_simple(
                event_type="home",
                action={"type": "home"},
                exec_fn=lambda: orig_press_home(self),
            )

        async def enter_patched(self):
            rec = recorder
            if rec is None:
                return await orig_press_enter(self)
            return await rec.record_type(text="\\n", exec_fn=lambda: orig_press_enter(self))

        AndroidDeviceController.tap = tap_patched
        AndroidDeviceController.swipe = swipe_patched
        AndroidDeviceController.input_text = input_text_patched
        AndroidDeviceController.launch_app = launch_app_patched
        AndroidDeviceController.open_url = open_url_patched
        AndroidDeviceController.press_back = back_patched
        AndroidDeviceController.press_home = home_patched
        AndroidDeviceController.press_enter = enter_patched

        try:
            profile = AgentProfile(name="default", from_file=str(llm_config_path))
            agent_config = (
                Builders.AgentConfig.with_default_profile(profile)
                .for_device(DevicePlatform.ANDROID, android_serial)
                .build()
            )
            agent = Agent(config=agent_config)
            await agent.init()

            task = (
                agent.new_task(user_goal)
                .with_name(task_name)
                # mobile-use's `max_steps` is used as LangGraph recursion_limit, which counts
                # internal graph iterations (not just UI actions). Use a larger multiplier to
                # avoid premature GRAPH_RECURSION_LIMIT failures for small MAS case budgets.
                .with_max_steps(int(max_steps) * 10)
                .with_trace_recording(enabled=True, path=str(trace_base_dir))
                .build()
            )
            out = await agent.run_task(request=task)

            trace_dir = _find_latest_minitap_trace_dir(trace_base_dir, task_name=task_name)
            return out, str(trace_dir) if trace_dir is not None else None
        finally:
            if agent is not None:
                try:
                    await agent.clean()
                except Exception:
                    pass
            AndroidDeviceController.tap = orig_tap
            AndroidDeviceController.swipe = orig_swipe
            AndroidDeviceController.input_text = orig_input_text
            AndroidDeviceController.launch_app = orig_launch_app
            AndroidDeviceController.open_url = orig_open_url
            AndroidDeviceController.press_back = orig_press_back
            AndroidDeviceController.press_home = orig_press_home
            AndroidDeviceController.press_enter = orig_press_enter

    return asyncio.run(_run())


def _find_latest_minitap_trace_dir(trace_base_dir: Path, *, task_name: str) -> Path | None:
    base = Path(trace_base_dir)
    if not base.exists():
        return None
    candidates: list[Path] = []
    prefix = f"{task_name}_"
    for p in base.iterdir():
        if not p.is_dir():
            continue
        if p.name.startswith(prefix):
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def create_adapter() -> MinitapMobileUseAdapter:
    return MinitapMobileUseAdapter()
