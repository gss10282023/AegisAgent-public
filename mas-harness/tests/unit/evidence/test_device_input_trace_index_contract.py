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


def test_device_input_trace_index_contract_l0_requires_ref_step_idx(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        writer.record_device_input_event(
            step_idx=0,
            ref_step_idx=0,
            source_level="L0",
            event_type="tap",
            payload={"coord_space": "physical_px", "x": 1, "y": 2},
            timestamp_ms=1,
            mapping_warnings=[],
        )

        events = _read_jsonl(writer.paths.device_input_trace)
        assert len(events) == 1
        assert events[0]["step_idx"] == 0
        assert events[0]["ref_step_idx"] == 0

        with pytest.raises(ValueError, match="ref_step_idx is required for L0"):
            writer.record_device_input_event(
                step_idx=1,
                ref_step_idx=None,
                source_level="L0",
                event_type="tap",
                payload={"coord_space": "physical_px", "x": 1, "y": 2},
                timestamp_ms=2,
                mapping_warnings=[],
            )

        with pytest.raises(ValueError, match="ref_step_idx must equal step_idx for L0"):
            writer.record_device_input_event(
                step_idx=1,
                ref_step_idx=0,
                source_level="L0",
                event_type="tap",
                payload={"coord_space": "physical_px", "x": 1, "y": 2},
                timestamp_ms=2,
                mapping_warnings=[],
            )
    finally:
        writer.close()


def test_device_input_trace_index_contract_l1_l2_step_idx_monotonic_unique(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        # L1: allow ref_step_idx = null.
        writer.record_device_input_event(
            step_idx=0,
            ref_step_idx=None,
            source_level="L1",
            event_type="tap",
            payload={"coord_space": "physical_px", "x": 1, "y": 2},
            timestamp_ms=1,
            mapping_warnings=[],
        )
        # L1/L2: allow 1:N mapping via duplicate ref_step_idx.
        writer.record_device_input_event(
            step_idx=1,
            ref_step_idx=0,
            source_level="L1",
            event_type="tap",
            payload={"coord_space": "physical_px", "x": 1, "y": 2},
            timestamp_ms=2,
            mapping_warnings=[],
        )
        writer.record_device_input_event(
            step_idx=2,
            ref_step_idx=0,
            source_level="L2",
            event_type="tap",
            payload={"coord_space": "physical_px", "x": 1, "y": 2},
            timestamp_ms=3,
            mapping_warnings=[],
        )

        events = _read_jsonl(writer.paths.device_input_trace)
        assert [e["step_idx"] for e in events] == [0, 1, 2]
        assert [e["ref_step_idx"] for e in events] == [None, 0, 0]

        # Any level: step_idx must be strictly increasing (unique).
        with pytest.raises(ValueError, match="step_idx must be strictly increasing"):
            writer.record_device_input_event(
                step_idx=2,
                ref_step_idx=None,
                source_level="L1",
                event_type="tap",
                payload={"coord_space": "physical_px", "x": 1, "y": 2},
                timestamp_ms=4,
                mapping_warnings=[],
            )

        with pytest.raises(ValueError, match="step_idx must be strictly increasing"):
            writer.record_device_input_event(
                step_idx=1,
                ref_step_idx=None,
                source_level="L2",
                event_type="tap",
                payload={"coord_space": "physical_px", "x": 1, "y": 2},
                timestamp_ms=4,
                mapping_warnings=[],
            )
    finally:
        writer.close()
