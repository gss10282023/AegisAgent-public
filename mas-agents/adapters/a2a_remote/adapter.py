from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Mapping
from uuid import uuid4

from mas_harness.evidence import EvidenceWriter, stable_sha256
from mas_harness.examples import ToyEnv
from mas_harness.oracles import make_oracle
from mas_harness.oracles.zoo.base import OracleContext, decision_from_evidence
from mas_harness.oracles.zoo.settings.boot_health import capture_device_infra
from mas_harness.oracles.zoo.utils.time_window import capture_episode_time
from mas_harness.runtime import reset_for_episode
from mas_harness.runtime.android.env_adapter import AndroidEnvAdapter
from mas_harness.spec import load_and_validate_case


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@contextmanager
def _temporary_environ(overrides: Mapping[str, str | None]):
    old = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _parse_adb_server(raw: str) -> tuple[str, int] | None:
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("tcp:"):
        s = s.removeprefix("tcp:").strip()
    if not s:
        return None

    if s.startswith("["):
        # Best-effort IPv6: [::1]:5037
        if "]" not in s:
            return None
        host, rest = s[1:].split("]", 1)
        host = host.strip()
        rest = rest.strip()
        if not host:
            return None
        if rest.startswith(":"):
            rest = rest[1:]
        port_str = rest.strip() or "5037"
    else:
        if ":" in s:
            host, port_str = s.rsplit(":", 1)
            host = host.strip()
            port_str = port_str.strip()
        else:
            host, port_str = s.strip(), "5037"

    if not host:
        return None
    try:
        port = int(port_str)
    except Exception:
        return None
    if port <= 0 or port > 65535:
        return None
    return host, port


def _resolve_adb_server() -> str:
    # Primary (recommended): explicit host:port.
    raw = os.environ.get("MAS_ADB_SERVER")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    # Secondary: adb client socket form.
    raw = os.environ.get("ADB_SERVER_SOCKET")
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if s.startswith("tcp:"):
            s = s.removeprefix("tcp:")
        return s.strip()

    host = os.environ.get("ADB_HOST")
    port = os.environ.get("ADB_PORT")
    if host and port:
        return f"{host.strip()}:{port.strip()}"

    return "host.docker.internal:5037"


def _resolve_android_serial(*, ctx) -> str:
    serial = str(getattr(ctx.phase0_cfg, "android_serial", "") or "").strip()
    if serial:
        return serial

    raw = os.environ.get("MAS_ANDROID_SERIAL") or os.environ.get("ANDROID_SERIAL")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    return "emulator-5554"


def _resolve_purple_url(*, ctx) -> str | None:
    raw = os.environ.get("MAS_PURPLE_URL") or os.environ.get("PURPLE_URL")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    raw = getattr(ctx.phase0_cfg, "agent_base_url", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    return None


_PORTAL_PREFLIGHT_LOCK = threading.Lock()
_PORTAL_PREFLIGHT_DONE = False
_PORTAL_PREFLIGHT_ATTEMPTS = 0


def _portal_preflight_enabled() -> bool:
    raw = str(os.getenv("MAS_PORTAL_PREFLIGHT", "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _run_portal_preflight_if_needed(
    *,
    writer: EvidenceWriter,
    purple_url: str,
    adb_server: str,
    android_serial: str,
) -> None:
    global _PORTAL_PREFLIGHT_ATTEMPTS
    global _PORTAL_PREFLIGHT_DONE

    if not _portal_preflight_enabled():
        return

    with _PORTAL_PREFLIGHT_LOCK:
        if _PORTAL_PREFLIGHT_DONE:
            return
        max_attempts_raw = os.getenv("MAS_PORTAL_PREFLIGHT_MAX_ATTEMPTS", "2")
        try:
            max_attempts = int(max_attempts_raw)
        except Exception:
            max_attempts = 2
        if _PORTAL_PREFLIGHT_ATTEMPTS >= max_attempts:
            return
        _PORTAL_PREFLIGHT_ATTEMPTS += 1

    timeout_s_raw = os.getenv("MAS_PORTAL_PREFLIGHT_TIMEOUT_S", "240")
    try:
        timeout_s = float(timeout_s_raw)
    except Exception:
        timeout_s = 240.0

    payload: Dict[str, Any] = {
        "case_id": "__portal_preflight__",
        "variant": {"mode": "portal_preflight"},
        "goal": "Portal preflight (install/enable only).",
        "adb_server": adb_server,
        "android_serial": android_serial,
        "timeouts": {"total_s": float(timeout_s), "max_steps": 1},
    }

    preflight_result: Dict[str, Any] | None = None
    try:
        preflight_result = _call_purple_a2a(
            base_url=purple_url, payload=payload, timeout_s=max(5.0, float(timeout_s))
        )
    except Exception as e:
        writer.record_device_event(
            {
                "event": "portal_preflight_error",
                "purple_url": purple_url,
                "adb_server": adb_server,
                "android_serial": android_serial,
                "error_type": type(e).__name__,
                "error": repr(e),
            }
        )
        return

    writer.record_device_event(
        {
            "event": "portal_preflight_result",
            "purple_url": purple_url,
            "adb_server": adb_server,
            "android_serial": android_serial,
            "result": preflight_result,
        }
    )

    with _PORTAL_PREFLIGHT_LOCK:
        if isinstance(preflight_result, dict) and preflight_result.get("status") == "success":
            _PORTAL_PREFLIGHT_DONE = True


def _run_async(factory):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    out: dict[str, Any] = {}
    err: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            out["value"] = asyncio.run(factory())
        except BaseException as e:
            err["error"] = e

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in err:
        raise err["error"]
    return out.get("value")


async def _call_purple_a2a_async(
    *,
    base_url: str,
    payload: Dict[str, Any],
    timeout_s: float,
) -> Dict[str, Any]:
    try:
        import httpx
        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.types import DataPart, Message, Part, Role, TextPart
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "a2a-sdk/httpx is required to run the a2a_remote adapter "
            "(install agentbeats/requirements.txt)"
        ) from e

    msg_text = _json_dumps(payload)

    async with httpx.AsyncClient(timeout=timeout_s) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
        agent_card = await resolver.get_agent_card()
        client = ClientFactory(ClientConfig(httpx_client=httpx_client, streaming=True)).create(
            agent_card
        )

        outbound_msg = Message(
            kind="message",
            role=Role.user,
            parts=[Part(root=TextPart(text=msg_text))],
            message_id=f"mas-a2a-remote-{uuid4().hex}",
        )

        last_event: object | None = None
        async for event in client.send_message(outbound_msg):
            last_event = event

    if last_event is None:
        raise RuntimeError("No events received from purple")

    parts: list[Part] = []
    if isinstance(last_event, Message):
        parts.extend(last_event.parts or [])
    elif isinstance(last_event, tuple) and len(last_event) == 2:
        task, _update = last_event
        status_msg = getattr(getattr(task, "status", None), "message", None)
        if status_msg is not None:
            parts.extend(getattr(status_msg, "parts", None) or [])
        for artifact in getattr(task, "artifacts", None) or []:
            parts.extend(getattr(artifact, "parts", None) or [])

    for part in parts:
        if isinstance(part.root, DataPart) and isinstance(part.root.data, dict):
            data = part.root.data
            if isinstance(data.get("status"), str) and isinstance(data.get("summary"), str):
                return data

    text_chunks: list[str] = []
    for part in parts:
        if isinstance(part.root, TextPart) and isinstance(part.root.text, str):
            if part.root.text.strip():
                text_chunks.append(part.root.text.strip())

    preview = "\n".join(text_chunks).strip()
    if preview:
        return {"status": "error", "summary": preview, "artifacts": {"raw_text": preview}}

    return {
        "status": "error",
        "summary": "Purple response did not include a TaskResult DataPart.",
        "artifacts": {"last_event_type": str(type(last_event))},
    }


def _call_purple_a2a(*, base_url: str, payload: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    timeout_s = max(0.1, float(timeout_s))

    def _factory():
        return asyncio.wait_for(
            _call_purple_a2a_async(base_url=base_url, payload=payload, timeout_s=timeout_s),
            timeout=max(0.1, timeout_s),
        )

    return _run_async(_factory)


class A2ARemoteAdapter:
    """MAS adapter that delegates episode execution to an external A2A purple agent."""

    def run_case(self, *, case_dir: Path, evidence_dir: Path, ctx) -> Dict[str, Any]:
        case_id = case_dir.name
        task: Dict[str, Any] = {}
        policy: Dict[str, Any] = {}

        try:
            specs = load_and_validate_case(case_dir=case_dir, schemas_dir=ctx.schemas_dir)
            task = specs.task
            policy = specs.policy
            case_id = str(task.get("task_id") or case_id)
        except Exception as e:
            # Keep going: still emit a valid evidence bundle + summary for debuggability.
            task = {"task_id": case_id, "user_goal": "", "success_oracle": {"type": "unknown"}}
            policy = {}
            load_error = {"type": type(e).__name__, "repr": repr(e)}
        else:
            load_error = None

        writer = EvidenceWriter(
            run_dir=ctx.output_dir,
            case_id=case_id,
            seed=ctx.seed,
            run_mode="phase3",
            metadata={
                **(ctx.run_metadata or {}),
                "agent_id": str(ctx.registry_entry.get("agent_id") or ""),
            },
            episode_dir=evidence_dir,
        )

        oracle_cfg = task.get("success_oracle") or task.get("oracle") or {}
        oracle = make_oracle(oracle_cfg)
        oracle_id = getattr(oracle, "oracle_id", str(oracle_cfg.get("type", "unknown")))
        oracle_type = getattr(oracle, "oracle_type", "unknown")

        steps_executed = 0
        terminated_reason = "unknown"
        infra_analysis: Dict[str, Any] = {"infra_failed": False, "infra_failure_reasons": []}
        error: Dict[str, Any] | None = None
        decision: Dict[str, Any] = {
            "success": False,
            "conclusive": False,
            "score": 0.0,
            "reason": "",
        }
        purple_result: Dict[str, Any] | None = None

        adb_server = _resolve_adb_server()
        adb_server_parsed = _parse_adb_server(adb_server)
        android_serial = _resolve_android_serial(ctx=ctx)
        purple_url = _resolve_purple_url(ctx=ctx)

        env: Any
        use_android = bool(str(getattr(ctx.phase0_cfg, "android_serial", "") or "").strip())
        if use_android:
            env = AndroidEnvAdapter(
                adb_path=str(getattr(ctx.phase0_cfg, "adb_path", "adb")),
                serial=str(getattr(ctx.phase0_cfg, "android_serial")),
                evidence_writer=writer,
                action_trace_level=getattr(ctx.phase0_cfg, "action_trace_level", None),
            )
        else:
            env = ToyEnv()

        env_overrides: dict[str, str | None] = {"ANDROID_SERIAL": android_serial}
        if adb_server_parsed is not None:
            host, port = adb_server_parsed
            env_overrides["ADB_SERVER_SOCKET"] = f"tcp:{host}:{port}"

        try:
            if load_error is not None:
                writer.record_device_event({"event": "case_load_error", **load_error})
                terminated_reason = "case_load_error"
                raise RuntimeError(load_error["repr"])

            if use_android and purple_url:
                _run_portal_preflight_if_needed(
                    writer=writer,
                    purple_url=purple_url,
                    adb_server=adb_server,
                    android_serial=android_serial,
                )

            with _temporary_environ(env_overrides):
                reset_event = reset_for_episode(
                    controller=env,
                    initial_state=task.get("initial_state"),
                    reset_strategy=ctx.phase0_cfg.reset_strategy,
                    snapshot_tag=ctx.phase0_cfg.snapshot_tag,
                )
                writer.record_reset(reset_event)

                episode_time, episode_time_event = capture_episode_time(
                    controller=env,
                    task_spec=task,
                )
                writer.record_device_event(episode_time_event)

                infra_event, infra_analysis = capture_device_infra(
                    env,
                    device_epoch_time_ms=episode_time.t0_device_epoch_ms,
                )
                writer.record_device_event(infra_event)

                oracle_ctx = OracleContext.from_task_and_controller(
                    task_spec=task,
                    controller=env,
                    episode_time=episode_time,
                    episode_dir=writer.root,
                )

                writer.record_oracle_events(oracle.pre_check(oracle_ctx))

                obs0 = env.observe()
                writer.record_observation(step=0, observation=obs0)
                set_current_obs_digest = getattr(env, "set_current_obs_digest", None)
                if callable(set_current_obs_digest):
                    set_current_obs_digest(writer.last_obs_digest)

                timeouts_total_s = float(policy.get("budgets", {}).get("max_seconds", 60))
                max_steps = int(
                    policy.get("budgets", {}).get("max_steps", task.get("max_steps", 50))
                )

                req_payload: Dict[str, Any] = {
                    "case_id": case_id,
                    "variant": {"case_dir": case_dir.name},
                    "goal": str(task.get("user_goal", "")),
                    "adb_server": adb_server,
                    "android_serial": android_serial,
                    "timeouts": {"total_s": timeouts_total_s, "max_steps": max_steps},
                }

                writer.record_device_event(
                    {
                        "event": "a2a_remote_request",
                        "purple_url": purple_url,
                        "adb_server": adb_server,
                        "android_serial": android_serial,
                    }
                )

                request_path = evidence_dir / "a2a_remote_request.json"
                request_path.write_text(_json_dumps(req_payload) + "\n", encoding="utf-8")

                input_digest = stable_sha256(req_payload)
                t0 = time.perf_counter()
                call_error: dict[str, Any] | None = None
                try:
                    if not purple_url:
                        raise RuntimeError(
                            "Missing purple A2A base URL (set MAS_AGENT_BASE_URL or MAS_PURPLE_URL)"
                        )
                    purple_result = _call_purple_a2a(
                        base_url=purple_url,
                        payload=req_payload,
                        timeout_s=timeouts_total_s,
                    )
                    terminated_reason = f"remote_{str(purple_result.get('status') or 'unknown')}"
                except Exception as e:
                    terminated_reason = "remote_error"
                    call_error = {"type": type(e).__name__, "repr": repr(e)}
                    purple_result = {
                        "status": "error",
                        "summary": f"Error calling purple: {e}",
                        "artifacts": {"error": call_error},
                    }
                latency_ms = int(round((time.perf_counter() - t0) * 1000.0))

                response_digest = stable_sha256(purple_result)
                writer.record_agent_call(
                    {
                        "step_idx": 0,
                        "agent_name": "a2a_remote",
                        "provider": "a2a_remote",
                        "model_id": None,
                        "base_url": purple_url,
                        "input_digest": input_digest,
                        "response_digest": response_digest,
                        "latency_ms": latency_ms,
                        "tokens_in": None,
                        "tokens_out": None,
                        "error": call_error,
                    }
                )

                response_path = evidence_dir / "a2a_remote_response.json"
                response_path.write_text(_json_dumps(purple_result) + "\n", encoding="utf-8")

                finished_action: Dict[str, Any] = {
                    "type": "finished",
                    "status": purple_result.get("status"),
                    "summary": purple_result.get("summary"),
                }
                writer.record_agent_action(step=0, action=finished_action)

                try:
                    obs1 = env.observe()
                    writer.record_observation(step=1, observation=obs1)
                    post_action: Dict[str, Any] = {
                        "type": "wait",
                        "duration_ms": 0,
                        "note": "a2a_remote_post_observation",
                    }
                    writer.record_agent_call(
                        {
                            "step_idx": 1,
                            "agent_name": "a2a_remote",
                            "provider": "a2a_remote",
                            "model_id": None,
                            "base_url": purple_url,
                            "input_digest": stable_sha256(
                                {
                                    "case_id": case_id,
                                    "step_idx": 1,
                                    "kind": "post_observation",
                                    "obs_digest": writer.last_obs_digest,
                                }
                            ),
                            "response_digest": stable_sha256(post_action),
                            "latency_ms": 0,
                            "tokens_in": None,
                            "tokens_out": None,
                            "error": None,
                        }
                    )
                    writer.record_agent_action(step=1, action=post_action)
                except Exception:
                    pass

                post_evidence = oracle.post_check(oracle_ctx)
                writer.record_oracle_events(post_evidence)
                decision = decision_from_evidence(
                    post_evidence,
                    oracle_id=getattr(oracle, "oracle_id", None),
                )
        except Exception as e:
            if error is None:
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
                "runner": "a2a_remote",
                "purple_url": purple_url,
                "adb_server": adb_server,
                "android_serial": android_serial,
                "purple_result": purple_result,
                "infra": infra_analysis,
                "error": error,
            },
        }

        summary = writer.write_summary(summary)
        writer.close()
        return summary


def create_adapter() -> A2ARemoteAdapter:
    return A2ARemoteAdapter()
