from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

from mas_harness.integration.agents.base import AgentAdapterError
from mas_harness.integration.agents.registry import discover_repo_root, load_agent_registry
from mas_harness.phases.phase0_artifacts import Phase0Config, ensure_phase0_artifacts
from mas_harness.runtime import runner


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase3 unified runner (runnable + audit_only ingest)."
    )
    parser.add_argument(
        "--agent_id", type=str, required=True, help="agent_id from agent_registry.yaml"
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=(
            "Optional override for agent_registry.yaml "
            "(default: mas-agents/registry/agent_registry.yaml)."
        ),
    )

    parser.add_argument(
        "--case_dir", type=Path, default=None, help="Case directory (runnable agents)."
    )
    parser.add_argument(
        "--trajectory",
        type=Path,
        default=None,
        help="Trajectory JSONL (audit_only agents).",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Output directory for run artifacts."
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="Random seed recorded in run_manifest.json."
    )
    parser.add_argument(
        "--schemas_dir",
        type=Path,
        default=Path("mas-spec/schemas"),
        help="Directory containing JSON schemas (default: mas-spec/schemas).",
    )
    parser.add_argument(
        "--dry_run_ingest_events",
        type=Path,
        default=None,
        help=(
            "Optional path to an agent_events_v1 JSONL file used to materialize "
            "device_input_trace.jsonl (L1) without relying on the agent-exported path_on_host."
        ),
    )
    parser.add_argument(
        "--comm_proxy_mode",
        type=str,
        default="off",
        choices=["off", "record"],
        help="Optional comm proxy recorder mode (default: off).",
    )
    parser.add_argument(
        "--strict_action_evidence",
        action="store_true",
        help="Fail the run when expected L0/L1/L2 action evidence is missing or invalid.",
    )

    parser.add_argument(
        "--execution_mode",
        type=str,
        default=os.environ.get("MAS_EXECUTION_MODE", "planner_only"),
        choices=["planner_only", "agent_driven"],
        help="Runner execution mode (default: planner_only)",
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
        "--env_profile",
        type=str,
        default=os.environ.get("MAS_ENV_PROFILE"),
        choices=["mas_core", "android_world_compat"],
        help="Optional env profile override recorded in run_manifest.json",
    )
    parser.add_argument("--android_serial", type=str, default=os.environ.get("MAS_ANDROID_SERIAL"))
    parser.add_argument("--adb_path", type=str, default=os.environ.get("MAS_ADB_PATH", "adb"))
    parser.add_argument("--snapshot_tag", type=str, default=os.environ.get("MAS_SNAPSHOT_TAG"))
    parser.add_argument(
        "--reset_strategy",
        type=str,
        default=os.environ.get("MAS_RESET_STRATEGY"),
        choices=["snapshot", "reinstall", "none"],
    )

    args = parser.parse_args(argv)

    if (args.case_dir is None) == (args.trajectory is None):
        parser.error("exactly one of --case_dir or --trajectory must be provided")

    agent_id = str(args.agent_id).strip()
    if not agent_id:
        parser.error("--agent_id must be non-empty")

    repo_root = discover_repo_root()
    registry_path = args.registry or (repo_root / "mas-agents" / "registry" / "agent_registry.yaml")
    entries = load_agent_registry(registry_path)
    registry_entry = runner._find_registry_entry(entries, agent_id)
    if registry_entry is None:
        raise SystemExit(f"agent_id not found in registry: {agent_id} ({registry_path})")

    availability = str(registry_entry.get("availability") or "").strip()
    if availability not in {"runnable", "audit_only", "unavailable"}:
        raise SystemExit(f"invalid registry availability for {agent_id}: {availability!r}")

    resolved_env_profile = (
        args.env_profile or str(registry_entry.get("env_profile") or "").strip() or "mas_core"
    )
    registry_action_trace_level = (
        str(registry_entry.get("action_trace_level") or "").strip() or None
    )

    if args.trajectory is not None:
        run_purpose = "ingest_only"
    else:
        case_dir = Path(args.case_dir).resolve() if args.case_dir is not None else None
        conformance_root = (repo_root / "mas-conformance").resolve()
        if case_dir and case_dir.is_relative_to(conformance_root):
            run_purpose = "conformance"
        else:
            run_purpose = "benchmark"

    if availability == "audit_only":
        evidence_trust_level = "agent_reported"
        oracle_source = "trajectory_declared"
    elif availability == "runnable":
        evidence_trust_level = "tcb_captured"
        oracle_source = "device_query"
    else:
        evidence_trust_level = "unknown"
        oracle_source = "none"

    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)

    dry_run_ingest_events: Path | None = None
    if args.dry_run_ingest_events is not None:
        dry_run_ingest_events = runner._resolve_path(repo_root, str(args.dry_run_ingest_events))

    phase0_cfg = Phase0Config(
        execution_mode=args.execution_mode,
        env_profile=resolved_env_profile,
        agent_name=agent_id,
        agent_provider=args.agent_provider,
        agent_model_id=args.agent_model_id,
        agent_base_url=args.agent_base_url,
        reset_strategy=args.reset_strategy,
        snapshot_tag=args.snapshot_tag,
        android_serial=args.android_serial,
        adb_path=args.adb_path,
        availability=availability,
        action_trace_level=registry_action_trace_level,
        action_trace_source=runner._infer_action_trace_source(
            repo_root=repo_root,
            registry_entry=registry_entry,
            availability=availability,
            action_trace_level=registry_action_trace_level,
        ),
        guard_enforcement="unenforced",
        evidence_trust_level=evidence_trust_level,
        oracle_source=oracle_source,
        run_purpose=run_purpose,
    )

    run_metadata = ensure_phase0_artifacts(
        out_dir=output,
        repo_root=repo_root,
        cfg=phase0_cfg,
        seed=int(args.seed),
    )

    if availability == "unavailable":
        reason = registry_entry.get("unavailable_reason") or "unavailable"
        runner._write_text(output / "unavailable_reason.txt", str(reason).strip() + "\n")
        print(f"[UNAVAILABLE] {agent_id}: {reason}")
        return 2

    try:
        if availability == "runnable":
            if args.case_dir is None:
                parser.error("--case_dir is required for runnable agents")
            if dry_run_ingest_events is not None and not dry_run_ingest_events.exists():
                parser.error(f"--dry_run_ingest_events not found: {dry_run_ingest_events}")
            runner._run_runnable(
                agent_id=agent_id,
                registry_entry=registry_entry,
                case_dir=args.case_dir,
                output=output,
                seed=int(args.seed),
                repo_root=repo_root,
                schemas_dir=args.schemas_dir,
                phase0_cfg=phase0_cfg,
                run_metadata=run_metadata,
                dry_run_ingest_events=dry_run_ingest_events,
                comm_proxy_mode=args.comm_proxy_mode,
                strict_action_evidence=bool(args.strict_action_evidence),
            )
            return 0

        if availability == "audit_only":
            if args.trajectory is None:
                parser.error("--trajectory is required for audit_only agents")
            if args.dry_run_ingest_events is not None:
                parser.error("--dry_run_ingest_events is only supported for runnable agents")
            runner._ingest_trajectory(
                agent_id=agent_id,
                registry_entry=registry_entry,
                trajectory=args.trajectory,
                output=output,
                seed=int(args.seed),
                repo_root=repo_root,
                schemas_dir=args.schemas_dir,
                phase0_cfg=phase0_cfg,
                run_metadata=run_metadata,
            )
            return 0
    except AgentAdapterError as e:
        print(f"[ERROR] {e}")
        return 2

    raise SystemExit(f"unhandled availability: {availability!r}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
