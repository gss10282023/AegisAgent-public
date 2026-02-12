from __future__ import annotations

import json
import sys
from pathlib import Path

from mas_harness.evidence.action_evidence.device_input_trace_validator import (
    validate_device_input_trace_jsonl,
)
from mas_harness.tools.audit_bundle import audit_bundle


def _run_cli(argv: list[str]) -> int:
    from mas_harness.cli import run_agent

    old_argv = sys.argv
    try:
        sys.argv = argv
        return int(run_agent.main())
    finally:
        sys.argv = old_argv


def _read_json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(obj, dict)
    return obj


def _assert_run_action_evidence(
    *,
    run_dir: Path,
    expected_level: str,
    expected_source: str,
) -> None:
    manifest = _read_json(run_dir / "run_manifest.json")
    assert manifest["action_trace_level"] == expected_level
    assert manifest["action_trace_source"] == expected_source
    assert "action_trace_degraded_from" not in manifest
    assert "action_trace_degraded_reason" not in manifest

    evidence_dir = run_dir / "episode_0000" / "evidence"
    trace_path = evidence_dir / "device_input_trace.jsonl"
    assert trace_path.exists()

    validate_device_input_trace_jsonl(
        trace_path,
        screen_trace_path=(evidence_dir / "screen_trace.jsonl"),
    )

    summary = _read_json(run_dir / "episode_0000" / "summary.json")
    assert summary["action_trace_level"] == expected_level
    assert summary["action_trace_source"] == expected_source

    assert audit_bundle(run_dir) == []


def _assert_run_action_evidence_degraded(
    *,
    run_dir: Path,
    degraded_from: str,
    degraded_reason: str,
) -> None:
    manifest = _read_json(run_dir / "run_manifest.json")
    assert manifest["action_trace_level"] == "none"
    assert manifest["action_trace_source"] == "none"
    assert manifest["action_trace_degraded_from"] == degraded_from
    assert manifest["action_trace_degraded_reason"] == degraded_reason

    evidence_dir = run_dir / "episode_0000" / "evidence"
    trace_path = evidence_dir / "device_input_trace.jsonl"
    assert trace_path.exists()

    validate_device_input_trace_jsonl(
        trace_path,
        screen_trace_path=(evidence_dir / "screen_trace.jsonl"),
    )

    summary = _read_json(run_dir / "episode_0000" / "summary.json")
    assert summary["action_trace_level"] == "none"
    assert summary["action_trace_source"] == "none"

    assert audit_bundle(run_dir) == []


def test_action_evidence_materialize_pipeline_l0_l1_l2(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    case_dir = repo_root / "mas-conformance" / "cases" / "conf_001_open_settings"

    out_l0 = tmp_path / "materialize_pipeline_l0"
    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "toy_agent",
            "--case_dir",
            str(case_dir),
            "--output",
            str(out_l0),
        ]
    )
    assert rc == 0
    _assert_run_action_evidence_degraded(
        run_dir=out_l0,
        degraded_from="L0",
        degraded_reason="empty_device_input_trace",
    )

    fixture = repo_root / "mas-harness" / "tests" / "fixtures" / "agent_events_l1_sample.jsonl"
    out_l1 = tmp_path / "materialize_pipeline_l1"
    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "toy_agent_driven_l1",
            "--case_dir",
            str(case_dir),
            "--output",
            str(out_l1),
            "--dry_run_ingest_events",
            str(fixture),
        ]
    )
    assert rc == 0
    _assert_run_action_evidence(
        run_dir=out_l1,
        expected_level="L1",
        expected_source="agent_events",
    )

    out_l2 = tmp_path / "materialize_pipeline_l2"
    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "toy_agent_driven_l2",
            "--case_dir",
            str(case_dir),
            "--output",
            str(out_l2),
            "--comm_proxy_mode",
            "record",
        ]
    )
    assert rc == 0
    _assert_run_action_evidence(
        run_dir=out_l2,
        expected_level="L2",
        expected_source="comm_proxy",
    )
