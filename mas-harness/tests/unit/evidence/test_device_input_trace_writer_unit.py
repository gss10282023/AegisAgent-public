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


def test_device_input_trace_writer_includes_ref_step_idx_top_level_even_when_null(
    tmp_path: Path,
) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        writer.record_device_input_event(
            0,
            None,
            "L1",
            "tap",
            {"coord_space": "physical_px", "x": 1, "y": 2},
            1,
            [],
        )
        events = _read_jsonl(writer.paths.device_input_trace)
        assert len(events) == 1
        assert "ref_step_idx" in events[0]
        assert events[0]["ref_step_idx"] is None
        assert "ref_step_idx" not in events[0].get("payload", {})
    finally:
        writer.close()


def test_device_input_trace_writer_l0_requires_ref_step_idx(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        with pytest.raises(ValueError, match="ref_step_idx is required for L0"):
            writer.record_device_input_event(
                0,
                None,
                "L0",
                "tap",
                {"coord_space": "physical_px", "x": 1, "y": 2},
                1,
                [],
            )
    finally:
        writer.close()


def test_device_input_trace_writer_l0_ref_step_idx_must_equal_step_idx(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        writer.record_device_input_event(
            0,
            0,
            "L0",
            "tap",
            {"coord_space": "physical_px", "x": 1, "y": 2},
            1,
            [],
        )

        with pytest.raises(ValueError, match="ref_step_idx must equal step_idx for L0"):
            writer.record_device_input_event(
                1,
                0,
                "L0",
                "tap",
                {"coord_space": "physical_px", "x": 1, "y": 2},
                2,
                [],
            )
    finally:
        writer.close()


def test_device_input_trace_writer_step_idx_must_be_strictly_increasing(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        writer.record_device_input_event(
            0,
            None,
            "L1",
            "tap",
            {"coord_space": "physical_px", "x": 1, "y": 2},
            1,
            [],
        )
        writer.record_device_input_event(
            1,
            None,
            "L2",
            "tap",
            {"coord_space": "physical_px", "x": 1, "y": 2},
            2,
            [],
        )

        with pytest.raises(ValueError, match="step_idx must be strictly increasing"):
            writer.record_device_input_event(
                1,
                None,
                "L1",
                "tap",
                {"coord_space": "physical_px", "x": 1, "y": 2},
                3,
                [],
            )

        with pytest.raises(ValueError, match="step_idx must be strictly increasing"):
            writer.record_device_input_event(
                0,
                None,
                "L2",
                "tap",
                {"coord_space": "physical_px", "x": 1, "y": 2},
                4,
                [],
            )
    finally:
        writer.close()


def test_device_input_trace_writer_l1_l2_allows_null_and_duplicate_ref_step_idx(
    tmp_path: Path,
) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        writer.record_device_input_event(
            0,
            None,
            "L1",
            "tap",
            {"coord_space": "physical_px", "x": 1, "y": 2},
            1,
            [],
        )
        writer.record_device_input_event(
            1,
            0,
            "L1",
            "tap",
            {"coord_space": "physical_px", "x": 1, "y": 2},
            2,
            [],
        )
        writer.record_device_input_event(
            2,
            0,
            "L2",
            "tap",
            {"coord_space": "physical_px", "x": 1, "y": 2},
            3,
            [],
        )

        events = _read_jsonl(writer.paths.device_input_trace)
        assert [e["step_idx"] for e in events] == [0, 1, 2]
        assert [e["ref_step_idx"] for e in events] == [None, 0, 0]
    finally:
        writer.close()
