from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas_harness.evidence.action_evidence.device_input_trace_validator import (
    DeviceInputTraceValidationError,
    validate_device_input_trace_jsonl,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    lines = [json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for r in rows]
    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def test_device_input_trace_schema_validation_accepts_valid_l0_trace(tmp_path: Path) -> None:
    screen_trace = tmp_path / "screen_trace.jsonl"
    _write_jsonl(
        screen_trace,
        [
            {
                "event": "screen",
                "ts_ms": 1,
                "step": 0,
                "physical_frame_boundary_px": {"left": 0, "top": 0, "right": 100, "bottom": 200},
            }
        ],
    )

    trace = tmp_path / "device_input_trace.jsonl"
    _write_jsonl(
        trace,
        [
            {
                "step_idx": 0,
                "ref_step_idx": 0,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 50, "y": 100},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            },
            {
                "step_idx": 1,
                "ref_step_idx": 1,
                "source_level": "L0",
                "event_type": "type",
                "payload": {"text": "hi"},
                "timestamp_ms": 2,
                "mapping_warnings": [],
            },
        ],
    )

    validate_device_input_trace_jsonl(trace, screen_trace_path=screen_trace)


def test_device_input_trace_schema_validation_rejects_missing_required_keys(tmp_path: Path) -> None:
    trace = tmp_path / "device_input_trace.jsonl"
    _write_jsonl(
        trace,
        [
            {
                "step_idx": 0,
                "ref_step_idx": 0,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 1, "y": 2},
                "timestamp_ms": 1,
                # missing mapping_warnings
            }
        ],
    )

    with pytest.raises(DeviceInputTraceValidationError):
        validate_device_input_trace_jsonl(trace)


def test_device_input_trace_schema_validation_l0_fails_on_missing_coords(tmp_path: Path) -> None:
    trace = tmp_path / "device_input_trace.jsonl"
    _write_jsonl(
        trace,
        [
            {
                "step_idx": 0,
                "ref_step_idx": 0,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": None, "y": None},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            }
        ],
    )

    msg = r"L0 coordinate events require resolved x/y"
    with pytest.raises(DeviceInputTraceValidationError, match=msg):
        validate_device_input_trace_jsonl(trace)


def test_device_input_trace_schema_validation_l0_fails_on_out_of_bounds(tmp_path: Path) -> None:
    screen_trace = tmp_path / "screen_trace.jsonl"
    _write_jsonl(
        screen_trace,
        [
            {
                "event": "screen",
                "ts_ms": 1,
                "step": 0,
                "physical_frame_boundary_px": {"left": 0, "top": 0, "right": 100, "bottom": 200},
            }
        ],
    )

    trace = tmp_path / "device_input_trace.jsonl"
    _write_jsonl(
        trace,
        [
            {
                "step_idx": 0,
                "ref_step_idx": 0,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 100, "y": 199},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            }
        ],
    )

    with pytest.raises(DeviceInputTraceValidationError, match=r"out of bounds"):
        validate_device_input_trace_jsonl(trace, screen_trace_path=screen_trace)


def test_device_input_trace_schema_validation_l1_missing_coord_requires_coord_unresolved_warning(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "device_input_trace.jsonl"
    _write_jsonl(
        trace,
        [
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L1",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": None, "y": None},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            }
        ],
    )

    with pytest.raises(DeviceInputTraceValidationError, match=r"coord_unresolved"):
        validate_device_input_trace_jsonl(trace)

    _write_jsonl(
        trace,
        [
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L1",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": None, "y": None},
                "timestamp_ms": 1,
                "mapping_warnings": ["coord_unresolved"],
            }
        ],
    )
    validate_device_input_trace_jsonl(trace)
