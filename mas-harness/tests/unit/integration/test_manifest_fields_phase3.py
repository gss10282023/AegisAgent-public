from __future__ import annotations

import json
import sys
from pathlib import Path


def _run_cli(argv: list[str]) -> int:
    from mas_harness.cli import run_agent

    old_argv = sys.argv
    try:
        sys.argv = argv
        return int(run_agent.main())
    finally:
        sys.argv = old_argv


def test_phase3_manifest_and_summary_fields_runnable(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    case_dir = repo_root / "mas-public" / "cases" / "smoke_001"
    out_dir = tmp_path / "phase3_smoke_toy"

    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "toy_agent",
            "--case_dir",
            str(case_dir),
            "--output",
            str(out_dir),
        ]
    )
    assert rc == 0

    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    for k in (
        "env_profile",
        "availability",
        "execution_mode",
        "eval_mode",
        "guard_enforced",
        "guard_unenforced_reason",
        "action_trace_level",
        "action_trace_source",
        "guard_enforcement",
        "evidence_trust_level",
        "oracle_source",
        "run_purpose",
    ):
        assert k in manifest, f"missing {k} in run_manifest.json"

    assert manifest["availability"] == "runnable"
    assert manifest["execution_mode"] == "planner_only"
    assert manifest["env_profile"] == "android_world_compat"
    assert manifest["eval_mode"] == "vanilla"
    assert manifest["guard_enforced"] is False
    assert manifest["guard_unenforced_reason"] == "guard_disabled"
    assert manifest["action_trace_level"] == "none"
    assert manifest["action_trace_source"] == "none"
    assert manifest["action_trace_degraded_from"] == "L0"
    assert manifest["action_trace_degraded_reason"] == "empty_device_input_trace"
    assert manifest["guard_enforcement"] == "unenforced"
    assert manifest["evidence_trust_level"] == "tcb_captured"
    assert manifest["oracle_source"] == "device_query"
    assert manifest["run_purpose"] == "benchmark"

    summary = json.loads((out_dir / "episode_0000" / "summary.json").read_text(encoding="utf-8"))
    for k in (
        "agent_id",
        "availability",
        "execution_mode",
        "eval_mode",
        "guard_enforced",
        "guard_unenforced_reason",
        "action_trace_level",
        "action_trace_source",
        "guard_enforcement",
        "env_profile",
        "evidence_trust_level",
        "oracle_source",
        "run_purpose",
        "oracle_decision",
        "agent_reported_finished",
        "task_success",
    ):
        assert k in summary, f"missing {k} in episode summary.json"

    assert summary["agent_id"] == "toy_agent"
    assert summary["availability"] == "runnable"
    assert summary["execution_mode"] == "planner_only"
    assert summary["eval_mode"] == "vanilla"
    assert summary["guard_enforced"] is False
    assert summary["guard_unenforced_reason"] == "guard_disabled"
    assert summary["action_trace_level"] == "none"
    assert summary["action_trace_source"] == "none"
    assert summary["guard_enforcement"] == "unenforced"
    assert summary["env_profile"] == "android_world_compat"
    assert summary["evidence_trust_level"] == "tcb_captured"
    assert summary["oracle_source"] == "device_query"
    assert summary["run_purpose"] == "benchmark"
    assert summary["oracle_decision"] in {"pass", "fail", "inconclusive", "not_applicable"}
    assert isinstance(summary["agent_reported_finished"], bool)
    if summary["oracle_decision"] == "pass":
        assert summary["task_success"] is True
    elif summary["oracle_decision"] == "fail":
        assert summary["task_success"] is False
    else:
        assert summary["task_success"] == "unknown"


def test_phase3_manifest_and_summary_fields_audit_only(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    traj = repo_root / "mas-harness" / "tests" / "fixtures" / "aw_traj_sample.jsonl"
    out_dir = tmp_path / "phase3_ingest_sample"

    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "some_audit_only_agent",
            "--trajectory",
            str(traj),
            "--output",
            str(out_dir),
        ]
    )
    assert rc == 0

    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    for k in (
        "env_profile",
        "availability",
        "execution_mode",
        "eval_mode",
        "guard_enforced",
        "guard_unenforced_reason",
        "action_trace_level",
        "action_trace_source",
        "guard_enforcement",
        "evidence_trust_level",
        "oracle_source",
        "run_purpose",
    ):
        assert k in manifest, f"missing {k} in run_manifest.json"

    assert manifest["availability"] == "audit_only"
    assert manifest["execution_mode"] == "planner_only"
    assert manifest["env_profile"] == "android_world_compat"
    assert manifest["eval_mode"] == "vanilla"
    assert manifest["guard_enforced"] is False
    assert manifest["guard_unenforced_reason"] == "guard_disabled"
    assert manifest["action_trace_level"] == "none"
    assert manifest["action_trace_source"] == "none"
    assert manifest["guard_enforcement"] == "unenforced"
    assert manifest["evidence_trust_level"] == "agent_reported"
    assert manifest["oracle_source"] == "trajectory_declared"
    assert manifest["run_purpose"] == "ingest_only"

    summary = json.loads((out_dir / "episode_0000" / "summary.json").read_text(encoding="utf-8"))
    for k in (
        "agent_id",
        "availability",
        "execution_mode",
        "eval_mode",
        "guard_enforced",
        "guard_unenforced_reason",
        "action_trace_level",
        "action_trace_source",
        "guard_enforcement",
        "env_profile",
        "evidence_trust_level",
        "oracle_source",
        "run_purpose",
        "oracle_decision",
        "agent_reported_finished",
        "task_success",
    ):
        assert k in summary, f"missing {k} in episode summary.json"

    assert summary["agent_id"] == "some_audit_only_agent"
    assert summary["availability"] == "audit_only"
    assert summary["execution_mode"] == "planner_only"
    assert summary["eval_mode"] == "vanilla"
    assert summary["guard_enforced"] is False
    assert summary["guard_unenforced_reason"] == "guard_disabled"
    assert summary["action_trace_level"] == "none"
    assert summary["action_trace_source"] == "none"
    assert summary["guard_enforcement"] == "unenforced"
    assert summary["env_profile"] == "android_world_compat"
    assert summary["evidence_trust_level"] == "agent_reported"
    assert summary["oracle_source"] == "trajectory_declared"
    assert summary["run_purpose"] == "ingest_only"
    assert summary["oracle_decision"] == "not_applicable"
    assert isinstance(summary["agent_reported_finished"], bool)
    assert summary["task_success"] == "unknown"
