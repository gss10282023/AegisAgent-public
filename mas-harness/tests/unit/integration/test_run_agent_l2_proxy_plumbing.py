from __future__ import annotations

import json
import sys
from pathlib import Path

from mas_harness.evidence.action_evidence.comm_proxy_trace import load_comm_proxy_trace_jsonl
from mas_harness.tools.audit_bundle import audit_bundle


def _run_cli(argv: list[str]) -> int:
    from mas_harness.cli import run_agent

    old_argv = sys.argv
    try:
        sys.argv = argv
        return int(run_agent.main())
    finally:
        sys.argv = old_argv


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        assert isinstance(obj, dict)
        out.append(obj)
    return out


def test_run_agent_l2_proxy_plumbing(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    case_dir = repo_root / "mas-conformance" / "cases" / "conf_001_open_settings"
    out_dir = tmp_path / "phase3_l2_proxy_plumbing"

    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "toy_agent_driven_l2",
            "--case_dir",
            str(case_dir),
            "--output",
            str(out_dir),
            "--comm_proxy_mode",
            "record",
        ]
    )
    assert rc == 0

    comm_path = out_dir / "episode_0000" / "evidence" / "comm_proxy_trace.jsonl"
    assert comm_path.exists()
    comm_events = load_comm_proxy_trace_jsonl(comm_path)
    assert any(e.get("direction") == "request" and e.get("endpoint") == "/act" for e in comm_events)

    trace_path = out_dir / "episode_0000" / "evidence" / "device_input_trace.jsonl"
    assert trace_path.exists()

    events = _read_jsonl(trace_path)
    assert len(events) == 1
    assert [e["step_idx"] for e in events] == [0]
    assert [e["source_level"] for e in events] == ["L2"]
    assert events[0]["ref_step_idx"] is None
    assert "ref_step_idx" not in events[0].get("payload", {})

    tap = events[0]
    assert tap["event_type"] == "tap"
    assert tap["payload"]["coord_space"] == "physical_px"
    assert tap["payload"]["x"] == 120
    assert tap["payload"]["y"] == 340
    assert tap["mapping_warnings"] == []

    ep_summary = json.loads((out_dir / "episode_0000" / "summary.json").read_text(encoding="utf-8"))
    assert ep_summary["action_trace_level"] == "L2"
    assert ep_summary["action_trace_source"] == "comm_proxy"
    assert ep_summary["action_evidence_mapping_stats"]["skipped_non_action_count"] > 0

    assert audit_bundle(out_dir) == []
