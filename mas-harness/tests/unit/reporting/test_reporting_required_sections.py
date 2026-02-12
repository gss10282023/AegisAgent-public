from __future__ import annotations

import json
from pathlib import Path

from mas_harness.reporting import write_action_evidence_distribution_report


def test_reporting_required_sections_action_evidence_distribution(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"

    # Episode 0: audit_only defaults to action_trace_level=none.
    ep0 = runs_dir / "run_0000" / "episode_0000"
    ep0.mkdir(parents=True, exist_ok=True)
    summary0 = {
        "agent_id": "agent_audit_only",
        "case_id": "c0",
        "availability": "audit_only",
        "status": "inconclusive",
        "oracle_decision": "not_applicable",
        "action_trace_level": "none",
        "action_trace_source": "none",
    }
    (ep0 / "summary.json").write_text(
        json.dumps(summary0, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (ep0 / "evidence").mkdir(parents=True, exist_ok=True)
    (ep0 / "evidence" / "summary.json").write_text(
        json.dumps(summary0, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Episode 1: runnable L2.
    ep1 = runs_dir / "run_0001" / "episode_0000"
    ep1.mkdir(parents=True, exist_ok=True)
    summary1 = {
        "agent_id": "agent_l2",
        "case_id": "c1",
        "availability": "runnable",
        "status": "success",
        "oracle_decision": "pass",
        "action_trace_level": "L2",
        "action_trace_source": "comm_proxy",
    }
    (ep1 / "summary.json").write_text(
        json.dumps(summary1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (ep1 / "evidence").mkdir(parents=True, exist_ok=True)
    (ep1 / "evidence" / "summary.json").write_text(
        json.dumps(summary1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    out_path = tmp_path / "report.json"
    payload = write_action_evidence_distribution_report(runs_dir=runs_dir, out_path=out_path)
    assert out_path.exists()

    assert "action_evidence_distribution" in payload
    dist = payload["action_evidence_distribution"]
    assert set(dist["buckets"].keys()) == {"L0", "L1", "L2", "none"}
    assert "L3" not in dist["buckets"]

    # Ensure we don't double-count by also including episode_*/evidence/summary.json.
    assert dist["total_episodes"] == 2
