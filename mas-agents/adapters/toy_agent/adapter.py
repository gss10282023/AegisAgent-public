from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from mas_harness.evidence import EvidenceWriter, stable_sha256
from mas_harness.runtime.android.env_adapter import AndroidEnvAdapter
from mas_harness.oracles.zoo.base import OracleContext, decision_from_evidence
from mas_harness.oracles.zoo.settings.boot_health import capture_device_infra
from mas_harness.oracles.zoo.utils.time_window import capture_episode_time
from mas_harness.oracles import make_oracle
from mas_harness.runtime import reset_for_episode
from mas_harness.spec.spec_loader import (
    discover_case,
    load_schema,
    load_yaml_or_json,
    validate_against_schema,
)
from mas_harness.examples import ToyAgent, ToyEnv


class ToyAgentAdapter:
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

        case_id = str(task["task_id"])

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

        env: Any
        if getattr(ctx.phase0_cfg, "android_serial", None):
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

            stop_after = int(oracle_cfg.get("steps", 2))
            agent = ToyAgent(stop_after=stop_after)
            agent.reset(user_goal=str(task.get("user_goal")), policy=policy, stop_after=stop_after)
            agent_name = type(agent).__name__
            policy_digest = stable_sha256(policy)
            user_goal = str(task.get("user_goal", ""))

            max_steps_task = int(task.get("max_steps", 20))
            max_steps_policy = int(policy.get("budgets", {}).get("max_steps", max_steps_task))
            max_steps = min(max_steps_task, max_steps_policy)

            terminated_reason = "max_steps"
            for step in range(max_steps):
                try:
                    obs = env.observe(
                        step=step,
                        dump_ui=(step == 0 or step % 5 == 0),
                    )
                except TypeError:
                    obs = env.observe()
                writer.record_observation(step=step, observation=obs)
                set_current_obs_digest = getattr(env, "set_current_obs_digest", None)
                if callable(set_current_obs_digest):
                    set_current_obs_digest(writer.last_obs_digest)

                screenshot_sha256 = stable_sha256(obs.get("screenshot_png") or b"")
                input_digest = stable_sha256(
                    {
                        "case_id": case_id,
                        "step_idx": step,
                        "user_goal": user_goal,
                        "policy_digest": policy_digest,
                        "ui_hash": obs.get("ui_hash"),
                        "screenshot_sha256": screenshot_sha256,
                    }
                )

                t0 = time.perf_counter()
                try:
                    action = agent.step(obs)
                except Exception as e:
                    latency_ms = int(round((time.perf_counter() - t0) * 1000.0))
                    writer.record_agent_call(
                        {
                            "step_idx": step,
                            "agent_name": agent_name,
                            "provider": None,
                            "model_id": None,
                            "base_url": None,
                            "input_digest": input_digest,
                            "response_digest": None,
                            "latency_ms": latency_ms,
                            "tokens_in": None,
                            "tokens_out": None,
                            "error": {"type": type(e).__name__, "repr": repr(e)},
                        }
                    )
                    raise

                latency_ms = int(round((time.perf_counter() - t0) * 1000.0))
                response_digest = stable_sha256(action)
                writer.record_agent_call(
                    {
                        "step_idx": step,
                        "agent_name": agent_name,
                        "provider": None,
                        "model_id": None,
                        "base_url": None,
                        "input_digest": input_digest,
                        "response_digest": response_digest,
                        "latency_ms": latency_ms,
                        "tokens_in": None,
                        "tokens_out": None,
                        "error": None,
                    }
                )
                normalized_action = writer.record_agent_action(step=step, action=action)
                if normalized_action.get("type") == "finished":
                    terminated_reason = "agent_stop"
                    break

                result = env.execute(normalized_action)
                writer.record_action(step=step, action=normalized_action, result=result)
                steps_executed += 1
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
                "runner": "toy_adapter",
                "infra": infra_analysis,
                "error": error,
                "agent_failed_reason": agent_failed_reason,
            },
        }
        summary = writer.write_summary(summary)
        writer.close()
        return summary


def create_adapter() -> ToyAgentAdapter:
    return ToyAgentAdapter()
