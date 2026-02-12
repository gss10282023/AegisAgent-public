from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import jsonschema
import pytest


def _load_schema() -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[4]
    schema_path = repo_root / "mas-harness/src/mas_harness/schemas/device_input_trace.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert isinstance(schema, dict)
    return schema


def test_device_input_trace_schema_loadable() -> None:
    schema = _load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)


def test_device_input_trace_schema_level_aware_ref_step_idx() -> None:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)

    l0_ok = {
        "step_idx": 12,
        "ref_step_idx": 12,
        "source_level": "L0",
        "event_type": "tap",
        "payload": {"x": 123, "y": 456, "coord_space": "physical_px"},
        "timestamp_ms": 1730000000000,
        "mapping_warnings": [],
    }
    validator.validate(l0_ok)

    l0_missing_ref = dict(l0_ok)
    l0_missing_ref.pop("ref_step_idx")
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(l0_missing_ref)

    l1_missing_ref = dict(l0_ok, source_level="L1")
    l1_missing_ref.pop("ref_step_idx")
    validator.validate(l1_missing_ref)

    l2_null_ref = dict(l0_ok, source_level="L2", ref_step_idx=None)
    validator.validate(l2_null_ref)
