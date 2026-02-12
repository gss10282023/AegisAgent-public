from __future__ import annotations

import json
from pathlib import Path

from mas_harness.evidence import EvidenceWriter
from mas_harness.evidence.action_evidence.comm_proxy_trace import load_comm_proxy_trace_jsonl
from mas_harness.evidence.action_evidence.l2_mapping import materialize_l2_device_input_trace


def _fixture_path() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "mas-harness" / "tests" / "fixtures" / "comm_proxy_l2_sample.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        assert isinstance(obj, dict)
        out.append(obj)
    return out


def test_l2_mapping_materializes_device_input_trace_and_writes_mapping_stats(
    tmp_path: Path,
) -> None:
    comm_path = tmp_path / "comm_proxy_trace.jsonl"
    comm_text_before = _fixture_path().read_text(encoding="utf-8")
    comm_path.write_text(comm_text_before, encoding="utf-8")

    events_in = load_comm_proxy_trace_jsonl(comm_path)

    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        stats = materialize_l2_device_input_trace(events_in, writer=writer)
        events_out = _read_jsonl(writer.paths.device_input_trace)

        assert len(events_out) == 1
        assert [e["step_idx"] for e in events_out] == [0]
        assert [e["source_level"] for e in events_out] == ["L2"]
        assert events_out[0]["ref_step_idx"] is None
        assert "ref_step_idx" not in events_out[0].get("payload", {})

        tap = events_out[0]
        assert tap["event_type"] == "tap"
        assert tap["payload"]["coord_space"] == "physical_px"
        assert tap["payload"]["x"] == 120
        assert tap["payload"]["y"] == 340
        assert tap["mapping_warnings"] == []

        summary = json.loads(writer.paths.summary.read_text(encoding="utf-8"))
        assert summary["action_evidence_mapping_stats"]["skipped_non_action_count"] > 0

        assert stats["input_total_count"] == 5
        assert stats["action_candidate_count"] == 1
        assert stats["mapped_action_count"] == 1
        assert stats["skipped_non_action_count"] == 4

        comm_text_after = comm_path.read_text(encoding="utf-8")
        assert comm_text_after == comm_text_before
        assert len([line for line in comm_text_after.splitlines() if line.strip()]) == 5
    finally:
        writer.close()


def test_l2_mapping_allows_ref_step_idx_repeat_for_1n(tmp_path: Path) -> None:
    events_in = [
        {
            "timestamp_ms": 1,
            "direction": "request",
            "endpoint": "/act",
            "payload": {
                "type": "tap",
                "x": 1,
                "y": 2,
                "coord_space": "physical_px",
                "ref_step_idx": 7,
            },
        },
        {
            "timestamp_ms": 2,
            "direction": "request",
            "endpoint": "/act",
            "payload": {
                "type": "tap",
                "x": 3,
                "y": 4,
                "coord_space": "physical_px",
                "ref_step_idx": 7,
            },
        },
    ]

    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        materialize_l2_device_input_trace(events_in, writer=writer)
        events_out = _read_jsonl(writer.paths.device_input_trace)
        assert [e["step_idx"] for e in events_out] == [0, 1]
        assert [e["ref_step_idx"] for e in events_out] == [7, 7]
        assert all("ref_step_idx" not in e.get("payload", {}) for e in events_out)
    finally:
        writer.close()


def test_l2_mapping_coord_unresolved_when_non_physical_coord_space(tmp_path: Path) -> None:
    events_in = [
        {
            "timestamp_ms": 1,
            "direction": "request",
            "endpoint": "/act",
            "payload": {"type": "tap", "coord_space": "screenshot_px", "x": 1, "y": 2},
        }
    ]

    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        materialize_l2_device_input_trace(events_in, writer=writer)
        events_out = _read_jsonl(writer.paths.device_input_trace)

        assert len(events_out) == 1
        assert events_out[0]["event_type"] == "tap"
        assert events_out[0]["payload"]["coord_space"] == "physical_px"
        assert events_out[0]["payload"]["x"] is None
        assert events_out[0]["payload"]["y"] is None
        assert "coord_unresolved" in events_out[0]["mapping_warnings"]
        assert any(
            w.startswith("coord_space_unconvertible:") for w in events_out[0]["mapping_warnings"]
        )
    finally:
        writer.close()


def test_l2_mapping_coord_unresolved_when_coord_space_missing(tmp_path: Path) -> None:
    events_in = [
        {
            "timestamp_ms": 1,
            "direction": "request",
            "endpoint": "/act",
            "payload": {"type": "tap", "x": 1, "y": 2},
        }
    ]

    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        materialize_l2_device_input_trace(events_in, writer=writer)
        events_out = _read_jsonl(writer.paths.device_input_trace)

        assert len(events_out) == 1
        assert events_out[0]["event_type"] == "tap"
        assert events_out[0]["payload"]["coord_space"] == "physical_px"
        assert events_out[0]["payload"]["x"] is None
        assert events_out[0]["payload"]["y"] is None
        assert "coord_unresolved" in events_out[0]["mapping_warnings"]
        assert any(
            w.startswith("coord_space_unconvertible:") for w in events_out[0]["mapping_warnings"]
        )
    finally:
        writer.close()
