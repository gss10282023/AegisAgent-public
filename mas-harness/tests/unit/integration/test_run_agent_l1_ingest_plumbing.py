from __future__ import annotations

import json
import sys
from pathlib import Path

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


def test_run_agent_materializes_l1_device_input_trace_from_dry_run_ingest(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    case_dir = repo_root / "mas-conformance" / "cases" / "conf_001_open_settings"
    fixture = repo_root / "mas-harness" / "tests" / "fixtures" / "agent_events_l1_sample.jsonl"
    out_dir = tmp_path / "phase3_l1_ingest_plumbing"

    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "toy_agent_driven_l1",
            "--case_dir",
            str(case_dir),
            "--output",
            str(out_dir),
            "--dry_run_ingest_events",
            str(fixture),
        ]
    )
    assert rc == 0

    trace_path = out_dir / "episode_0000" / "evidence" / "device_input_trace.jsonl"
    assert trace_path.exists()

    events = _read_jsonl(trace_path)
    assert len(events) == 8
    assert [e["step_idx"] for e in events] == list(range(8))
    assert [e["source_level"] for e in events] == ["L1"] * 8
    assert all("ref_step_idx" in e and e["ref_step_idx"] is None for e in events)

    assert [e["event_type"] for e in events] == [
        "tap",
        "swipe",
        "type",
        "back",
        "home",
        "open_app",
        "open_url",
        "wait",
    ]

    tap = events[0]
    assert tap["payload"]["coord_space"] == "physical_px"
    assert tap["payload"]["x"] == 120
    assert tap["payload"]["y"] == 340

    assert audit_bundle(out_dir) == []
