from __future__ import annotations

import json
from pathlib import Path

from mas_harness.evidence import EvidenceWriter
from mas_harness.evidence.action_evidence.agent_events_v1 import load_agent_events_v1_jsonl
from mas_harness.evidence.action_evidence.l1_mapping import materialize_l1_device_input_trace


def _fixture_path() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "mas-harness" / "tests" / "fixtures" / "agent_events_l1_sample.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        assert isinstance(obj, dict)
        out.append(obj)
    return out


def test_l1_mapping_materializes_device_input_trace_for_fixture(tmp_path: Path) -> None:
    raw_events = load_agent_events_v1_jsonl(_fixture_path())

    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        materialize_l1_device_input_trace(raw_events, writer=writer)
        events = _read_jsonl(writer.paths.device_input_trace)

        assert len(events) == 8
        assert [e["step_idx"] for e in events] == list(range(8))
        assert [e["source_level"] for e in events] == ["L1"] * 8
        assert all("ref_step_idx" in e and e["ref_step_idx"] is None for e in events)
        assert all("ref_step_idx" not in e.get("payload", {}) for e in events)

        tap = events[0]
        assert tap["event_type"] == "tap"
        assert tap["payload"]["coord_space"] == "physical_px"
        assert tap["payload"]["x"] == 120
        assert tap["payload"]["y"] == 340
        assert tap["mapping_warnings"] == []

        swipe = events[1]
        assert swipe["event_type"] == "swipe"
        assert swipe["payload"]["coord_space"] == "physical_px"
        assert swipe["payload"]["start"] == {"x": 100, "y": 200}
        assert swipe["payload"]["end"] == {"x": 300, "y": 400}
        assert swipe["mapping_warnings"] == []

        assert events[2]["event_type"] == "type"
        assert events[2]["payload"]["text"] == "hello world"

        assert events[3]["event_type"] == "back"
        assert events[4]["event_type"] == "home"
        assert events[5]["event_type"] == "open_app"
        assert events[6]["event_type"] == "open_url"
        assert events[7]["event_type"] == "wait"
        assert events[7]["payload"]["duration_ms"] == 1000
    finally:
        writer.close()


def test_l1_mapping_no_silent_drop_for_unknown_event_type(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        materialize_l1_device_input_trace(
            [{"timestamp_ms": 1, "type": "mystery"}],
            writer=writer,
        )
        events = _read_jsonl(writer.paths.device_input_trace)
        assert len(events) == 1
        assert events[0]["event_type"] == "wait"
        assert "event_type_unsupported:mystery" in events[0]["mapping_warnings"]
    finally:
        writer.close()


def test_l1_mapping_coord_unresolved_when_non_physical_coord_space(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        materialize_l1_device_input_trace(
            [{"timestamp_ms": 1, "type": "tap", "coord_space": "screenshot_px", "x": 1, "y": 2}],
            writer=writer,
        )
        events = _read_jsonl(writer.paths.device_input_trace)
        assert len(events) == 1
        assert events[0]["event_type"] == "tap"
        assert events[0]["payload"]["coord_space"] == "physical_px"
        assert events[0]["payload"]["x"] is None
        assert events[0]["payload"]["y"] is None
        assert "coord_unresolved" in events[0]["mapping_warnings"]
        assert any(
            w.startswith("coord_space_unconvertible:") for w in events[0]["mapping_warnings"]
        )
    finally:
        writer.close()


def test_l1_mapping_rounds_float_physical_px_coords(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        materialize_l1_device_input_trace(
            [
                {
                    "timestamp_ms": 1,
                    "type": "tap",
                    "coord_space": "physical_px",
                    "x": 120.2,
                    "y": 340.7,
                }
            ],
            writer=writer,
        )
        events = _read_jsonl(writer.paths.device_input_trace)
        assert len(events) == 1
        assert events[0]["payload"]["x"] == 120
        assert events[0]["payload"]["y"] == 341
        assert events[0]["mapping_warnings"] == []
    finally:
        writer.close()
