from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas_harness.evidence import EvidenceWriter


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        assert isinstance(obj, dict)
        out.append(obj)
    return out


def test_device_input_trace_coord_strictness_matrix_l0_strict_fails_on_missing_coord(
    tmp_path: Path,
) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        with pytest.raises(ValueError, match=r"payload\.x/y must be int"):
            writer.record_device_input_event(
                step_idx=0,
                ref_step_idx=0,
                source_level="L0",
                event_type="tap",
                payload={"coord_space": "physical_px"},
                timestamp_ms=1,
                mapping_warnings=[],
            )
    finally:
        writer.close()


def test_device_input_trace_coord_strictness_matrix_l0_strict_fails_on_mapping_warnings(
    tmp_path: Path,
) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        with pytest.raises(
            ValueError, match=r"mapping_warnings must be empty for L0 coordinate events"
        ):
            writer.record_device_input_event(
                step_idx=0,
                ref_step_idx=0,
                source_level="L0",
                event_type="tap",
                payload={"coord_space": "physical_px", "x": 1, "y": 2},
                timestamp_ms=1,
                mapping_warnings=["coord_unresolved"],
            )
    finally:
        writer.close()


def test_device_input_trace_coord_strictness_matrix_requires_physical_px_coord_space(
    tmp_path: Path,
) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        with pytest.raises(ValueError, match=r"payload\.coord_space must be 'physical_px'"):
            writer.record_device_input_event(
                step_idx=0,
                ref_step_idx=0,
                source_level="L0",
                event_type="tap",
                payload={"coord_space": "screen_px", "x": 1, "y": 2},
                timestamp_ms=1,
                mapping_warnings=[],
            )
    finally:
        writer.close()


def test_coord_strictness_l1_l2_forbids_coord_unresolved_warning_when_resolved(
    tmp_path: Path,
) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        with pytest.raises(
            ValueError, match=r"mapping_warnings includes 'coord_unresolved' but coord is present"
        ):
            writer.record_device_input_event(
                step_idx=0,
                ref_step_idx=None,
                source_level="L1",
                event_type="tap",
                payload={"coord_space": "physical_px", "x": 1, "y": 2},
                timestamp_ms=1,
                mapping_warnings=["coord_unresolved"],
            )
    finally:
        writer.close()


def test_coord_strictness_l1_l2_tolerant_requires_warning_on_missing_coord(
    tmp_path: Path,
) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        with pytest.raises(ValueError, match=r"must include 'coord_unresolved'"):
            writer.record_device_input_event(
                step_idx=0,
                ref_step_idx=None,
                source_level="L1",
                event_type="tap",
                payload={"coord_space": "physical_px", "x": None, "y": None},
                timestamp_ms=1,
                mapping_warnings=[],
            )

        writer.record_device_input_event(
            step_idx=0,
            ref_step_idx=None,
            source_level="L1",
            event_type="tap",
            payload={"coord_space": "physical_px", "x": None, "y": None},
            timestamp_ms=1,
            mapping_warnings=["coord_unresolved"],
        )

        writer.record_device_input_event(
            step_idx=1,
            ref_step_idx=None,
            source_level="L2",
            event_type="tap",
            payload={"coord_space": "physical_px", "x": None, "y": None},
            timestamp_ms=2,
            mapping_warnings=["coord_unresolved"],
        )

        events = _read_jsonl(writer.paths.device_input_trace)
        assert len(events) == 2
        assert events[0]["source_level"] == "L1"
        assert events[0]["payload"]["x"] is None
        assert events[0]["payload"]["y"] is None
        assert "coord_unresolved" in events[0]["mapping_warnings"]
        assert events[1]["source_level"] == "L2"
        assert events[1]["payload"]["x"] is None
        assert events[1]["payload"]["y"] is None
        assert "coord_unresolved" in events[1]["mapping_warnings"]
    finally:
        writer.close()


def test_device_input_trace_coord_strictness_matrix_swipe_strictness_matrix(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        with pytest.raises(ValueError, match=r"payload\.start/end must be int"):
            writer.record_device_input_event(
                step_idx=0,
                ref_step_idx=0,
                source_level="L0",
                event_type="swipe",
                payload={
                    "coord_space": "physical_px",
                    "start": {"x": 1, "y": 2},
                    "end": {"x": None, "y": None},
                },
                timestamp_ms=1,
                mapping_warnings=[],
            )

        with pytest.raises(ValueError, match=r"must include 'coord_unresolved'"):
            writer.record_device_input_event(
                step_idx=0,
                ref_step_idx=None,
                source_level="L1",
                event_type="swipe",
                payload={
                    "coord_space": "physical_px",
                    "start": {"x": None, "y": None},
                    "end": {"x": None, "y": None},
                },
                timestamp_ms=1,
                mapping_warnings=[],
            )

        writer.record_device_input_event(
            step_idx=0,
            ref_step_idx=None,
            source_level="L1",
            event_type="swipe",
            payload={
                "coord_space": "physical_px",
                "start": {"x": None, "y": None},
                "end": {"x": None, "y": None},
            },
            timestamp_ms=1,
            mapping_warnings=["coord_unresolved"],
        )

        events = _read_jsonl(writer.paths.device_input_trace)
        assert len(events) == 1
        assert events[0]["event_type"] == "swipe"
        assert events[0]["payload"]["start"]["x"] is None
        assert events[0]["payload"]["start"]["y"] is None
        assert events[0]["payload"]["end"]["x"] is None
        assert events[0]["payload"]["end"]["y"] is None
        assert "coord_unresolved" in events[0]["mapping_warnings"]
    finally:
        writer.close()
