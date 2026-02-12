from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from mas_harness.evidence import EvidenceWriter, stable_sha256
from mas_harness.examples import ToyAgent, ToyEnv
from mas_harness.oracle_framework.audit_engine import AuditEngine
from mas_harness.oracles import make_oracle
from mas_harness.oracles.zoo.base import OracleContext, decision_from_evidence
from mas_harness.oracles.zoo.settings.boot_health import capture_device_infra
from mas_harness.oracles.zoo.utils.time_window import capture_episode_time
from mas_harness.phases.phase0_artifacts import Phase0Config, ensure_phase0_artifacts
from mas_harness.runtime import reset_for_episode
from mas_harness.spec.spec_loader import (
    SpecValidationError,
    discover_case,
    load_schema,
    load_yaml_or_json,
    validate_against_schema,
)
from mas_harness.spec.validate_specs import iter_case_dirs


def run_case(
    *,
    case_dir: Path,
    out_dir: Path,
    seed: int,
    schemas_dir: Path,
    phase0_cfg: Phase0Config | None = None,
    repo_root: Path | None = None,
    controller: Any | None = None,
) -> Dict[str, Any]:
    paths = discover_case(case_dir)

    # Load schemas
    task_schema = load_schema(schemas_dir / "task_schema.json")
    policy_schema = load_schema(schemas_dir / "policy_schema.json")
    eval_schema = load_schema(schemas_dir / "eval_schema.json")
    attack_schema = load_schema(schemas_dir / "attack_schema.json")

    # Load specs
    task = load_yaml_or_json(paths.task)
    policy = load_yaml_or_json(paths.policy)
    ev = load_yaml_or_json(paths.eval)

    # Validate
    validate_against_schema(task, task_schema, where=str(paths.task))
    validate_against_schema(policy, policy_schema, where=str(paths.policy))
    validate_against_schema(ev, eval_schema, where=str(paths.eval))
    attack: Dict[str, Any] | None = None
    if paths.attack is not None:
        attack = load_yaml_or_json(paths.attack)
        validate_against_schema(attack, attack_schema, where=str(paths.attack))

    case_id = str(task["task_id"])

    # Phase 0 governance artifacts (run-level)
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    if phase0_cfg is None:
        phase0_cfg = Phase0Config()
    phase0_meta = ensure_phase0_artifacts(
        out_dir=out_dir,
        repo_root=repo_root,
        cfg=phase0_cfg,
        seed=seed,
    )

    writer = EvidenceWriter(
        run_dir=out_dir,
        case_id=case_id,
        seed=seed,
        run_mode="public",
        metadata={
            **phase0_meta,
        },
    )

    # Phase-0 runner uses ToyEnv/ToyAgent so CI does not require Android.
    env = controller if controller is not None else ToyEnv()

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
    decision: Dict[str, Any] = {"success": False, "conclusive": False, "score": 0.0, "reason": ""}

    try:
        reset_event = reset_for_episode(
            controller=env,
            initial_state=task.get("initial_state"),
            reset_strategy=phase0_cfg.reset_strategy,
            snapshot_tag=phase0_cfg.snapshot_tag,
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

        # Pre-check oracle (record evidence)
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

    except Exception as e:  # pragma: no cover
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
        "case_id": case_id,
        "seed": seed,
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
            "runner": "toy",
            "infra": infra_analysis,
            "error": error,
            "agent_failed_reason": agent_failed_reason,
        },
    }
    summary = writer.write_summary(summary)
    writer.close()

    success_oracle_cfg = task.get("success_oracle") or task.get("oracle") or {}
    success_oracle_name = None
    if isinstance(success_oracle_cfg, dict):
        v = success_oracle_cfg.get("plugin") or success_oracle_cfg.get("type")
        if isinstance(v, str) and v.strip():
            success_oracle_name = v.strip()

    audit_ctx: Dict[str, Any] = {
        "case_id": case_id,
        "task": task,
        "policy": policy,
        "eval": ev,
        "success_oracle_name": success_oracle_name,
    }
    if isinstance(attack, dict):
        audit_ctx["attack"] = attack
        if isinstance(attack.get("impact_level"), str) and attack.get("impact_level").strip():
            audit_ctx["impact_level"] = attack.get("impact_level").strip()

    AuditEngine().run(episode_dir=writer.root, case_ctx=audit_ctx)
    updated = None
    try:
        updated_obj = json.loads((writer.root / "summary.json").read_text(encoding="utf-8"))
        if isinstance(updated_obj, dict):
            updated = updated_obj
    except Exception:
        updated = None
    return updated or summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MAS public cases (phase-0 scaffold).")
    parser.add_argument(
        "--cases_dir",
        type=Path,
        required=True,
        help="Case directory or a folder containing cases",
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        required=True,
        help="Output directory for runs",
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="Global seed",
    )
    parser.add_argument(
        "--schemas_dir",
        type=Path,
        default=Path("mas-spec/schemas"),
        help="Directory containing JSON schemas (default: mas-spec/schemas)",
    )

    # Phase 0.7 + Phase 3 (future) config surface. Defaults keep Phase-0 toy runs working.
    parser.add_argument(
        "--execution_mode",
        type=str,
        default=os.environ.get("MAS_EXECUTION_MODE", "planner_only"),
        choices=["planner_only", "agent_driven"],
        help="Runner execution mode (default: planner_only)",
    )
    parser.add_argument(
        "--env_profile",
        type=str,
        default=os.environ.get("MAS_ENV_PROFILE", "mas_core"),
        choices=["mas_core", "android_world_compat"],
        help="Env profile id to record in run_manifest.json (default: mas_core)",
    )
    parser.add_argument(
        "--agent_name",
        type=str,
        default=os.environ.get("MAS_AGENT_NAME", "toy_agent"),
        help="Agent name to record in run_manifest.json",
    )
    parser.add_argument(
        "--agent_provider",
        type=str,
        default=os.environ.get("MAS_AGENT_PROVIDER"),
        help="External provider id (e.g., openrouter/local). Recorded in run_manifest.json.",
    )
    parser.add_argument(
        "--agent_model_id",
        type=str,
        default=os.environ.get("MAS_AGENT_MODEL_ID"),
        help="Provider-facing model identifier. Recorded in run_manifest.json.",
    )
    parser.add_argument(
        "--agent_base_url",
        type=str,
        default=os.environ.get("MAS_AGENT_BASE_URL"),
        help="Provider base URL. Recorded in run_manifest.json.",
    )
    parser.add_argument(
        "--android_serial",
        type=str,
        default=os.environ.get("MAS_ANDROID_SERIAL"),
        help="adb device serial (optional; enables env capability probing)",
    )
    parser.add_argument(
        "--adb_path",
        type=str,
        default=os.environ.get("MAS_ADB_PATH", "adb"),
        help="Path to adb binary (default: adb)",
    )
    parser.add_argument(
        "--snapshot_tag",
        type=str,
        default=os.environ.get("MAS_SNAPSHOT_TAG"),
        help="AVD snapshot tag (optional; recorded for reproducibility)",
    )
    parser.add_argument(
        "--reset_strategy",
        type=str,
        default=os.environ.get("MAS_RESET_STRATEGY"),
        choices=["snapshot", "reinstall", "none"],
        help=(
            "Reset strategy (snapshot/reinstall/none). Defaults to snapshot "
            "when snapshot_tag is set."
        ),
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    phase0_cfg = Phase0Config(
        execution_mode=args.execution_mode,
        env_profile=args.env_profile,
        agent_name=args.agent_name,
        agent_provider=args.agent_provider,
        agent_model_id=args.agent_model_id,
        agent_base_url=args.agent_base_url,
        reset_strategy=args.reset_strategy,
        snapshot_tag=args.snapshot_tag,
        android_serial=args.android_serial,
        adb_path=args.adb_path,
    )

    # Ensure Phase-0 run-level artifacts exist even if a later case fails.
    repo_root = Path(__file__).resolve().parents[3]
    ensure_phase0_artifacts(
        out_dir=args.out_dir,
        repo_root=repo_root,
        cfg=phase0_cfg,
        seed=args.seed,
    )

    summaries: List[Dict[str, Any]] = []
    for case_dir in iter_case_dirs(args.cases_dir):
        try:
            summary = run_case(
                case_dir=case_dir,
                out_dir=args.out_dir,
                seed=args.seed,
                schemas_dir=args.schemas_dir,
                phase0_cfg=phase0_cfg,
                repo_root=repo_root,
            )
            summaries.append(summary)
            print(f"[OK] {summary['case_id']} steps={summary['steps']} status={summary['status']}")
        except SpecValidationError as e:
            raise SystemExit(f"Case failed validation: {case_dir}\n{e}")

    # Write a top-level index for convenience
    index_path = args.out_dir / "index.json"
    index_path.write_text(
        json.dumps({"seed": args.seed, "cases": summaries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
