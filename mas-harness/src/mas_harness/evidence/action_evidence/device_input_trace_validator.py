from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema


class DeviceInputTraceValidationError(ValueError):
    """Raised when a device_input_trace.jsonl line violates the Phase-3 contract."""


_SOURCE_LEVEL_VALUES = {"L0", "L1", "L2"}
_COORD_EVENT_TYPES = {"tap", "long_press", "swipe"}


def _schema_path() -> Path:
    # mas_harness/evidence/action_evidence/* → mas_harness/schemas/device_input_trace.schema.json
    return Path(__file__).resolve().parents[2] / "schemas" / "device_input_trace.schema.json"


@lru_cache(maxsize=1)
def _load_schema() -> dict[str, Any]:
    schema_path = _schema_path()
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DeviceInputTraceValidationError(f"schema must be an object: {schema_path}")
    jsonschema.Draft202012Validator.check_schema(data)
    return data


def _coerce_int_strict(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _load_physical_frame_bounds_by_step(screen_trace_path: Path) -> dict[int, dict[str, int]]:
    bounds_by_step: dict[int, dict[str, int]] = {}
    for line in screen_trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict) or obj.get("event") != "screen":
            continue

        step = _coerce_int_strict(obj.get("step"))
        boundary_raw = obj.get("physical_frame_boundary_px")
        if step is None or not isinstance(boundary_raw, dict):
            continue

        left = _coerce_int_strict(boundary_raw.get("left"))
        top = _coerce_int_strict(boundary_raw.get("top"))
        right = _coerce_int_strict(boundary_raw.get("right"))
        bottom = _coerce_int_strict(boundary_raw.get("bottom"))
        if None in (left, top, right, bottom):
            continue

        bounds_by_step[int(step)] = {
            "left": int(left),
            "top": int(top),
            "right": int(right),
            "bottom": int(bottom),
        }
    return bounds_by_step


def validate_device_input_trace_jsonl(
    path: Path,
    *,
    screen_trace_path: Path | None = None,
) -> None:
    """Validate a device_input_trace.jsonl file (Phase3 3b-6a).

    Validation includes:
      - JSON parse + JSONSchema (single schema shared by L0/L1/L2)
      - Level-aware coordinate strictness:
          * L0: missing/out-of-bounds coords -> fail
          * L1/L2: missing coords allowed but must include 'coord_unresolved' warning
      - coord_space for coordinate events must be 'physical_px'
    """

    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)

    bounds_by_step: dict[int, dict[str, int]] = {}
    if screen_trace_path is not None and screen_trace_path.exists():
        bounds_by_step = _load_physical_frame_bounds_by_step(screen_trace_path)

    errors: list[str] = []
    last_step_idx: int | None = None

    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue

        try:
            obj = json.loads(line)
        except Exception as e:
            errors.append(f"{path}:{line_no}: invalid json ({e})")
            continue

        if not isinstance(obj, dict):
            errors.append(f"{path}:{line_no}: jsonl line must be an object")
            continue

        schema_errors = sorted(validator.iter_errors(obj), key=lambda err: list(err.path))
        for err in schema_errors:
            loc = "/".join(str(p) for p in err.path)
            suffix = f":{loc}" if loc else ""
            errors.append(f"{path}:{line_no}{suffix}: {err.message}")

        step_idx = obj.get("step_idx")
        step_idx_int = _coerce_int_strict(step_idx)
        if step_idx_int is None:
            # Leave the concrete message to schema errors; avoid cascaded failures.
            continue

        if last_step_idx is not None and step_idx_int <= last_step_idx:
            errors.append(f"{path}:{line_no}: step_idx must be strictly increasing")
        last_step_idx = int(step_idx_int)

        source_level_raw = obj.get("source_level")
        level = source_level_raw if isinstance(source_level_raw, str) else ""
        if level not in _SOURCE_LEVEL_VALUES:
            continue

        event_type_raw = obj.get("event_type")
        if not isinstance(event_type_raw, str) or not event_type_raw.strip():
            continue
        event_type = event_type_raw.strip().lower()

        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue

        mapping_warnings = obj.get("mapping_warnings")
        warnings_list: list[str] = []
        if isinstance(mapping_warnings, list):
            warnings_list = [w for w in mapping_warnings if isinstance(w, str) and w.strip()]

        if event_type not in _COORD_EVENT_TYPES:
            continue

        coord_space = payload.get("coord_space")
        if not isinstance(coord_space, str) or coord_space.strip() != "physical_px":
            errors.append(
                f"{path}:{line_no}: payload.coord_space must be 'physical_px' for coordinate events"
            )
            continue

        if event_type in {"tap", "long_press"}:
            if "x" not in payload or "y" not in payload:
                errors.append(f"{path}:{line_no}: missing payload.x/y for {event_type}")
                continue

            x_int = _coerce_int_strict(payload.get("x"))
            y_int = _coerce_int_strict(payload.get("y"))
            coord_unresolved = x_int is None or y_int is None

            if coord_unresolved:
                if payload.get("x") is not None or payload.get("y") is not None:
                    errors.append(f"{path}:{line_no}: unresolved coord must use null x/y")
                if level == "L0":
                    errors.append(f"{path}:{line_no}: L0 coordinate events require resolved x/y")
                else:
                    if "coord_unresolved" not in set(warnings_list):
                        errors.append(
                            f"{path}:{line_no}: L1/L2 missing coord requires 'coord_unresolved' "
                            "warning"
                        )
            else:
                if level == "L0":
                    if warnings_list:
                        errors.append(
                            f"{path}:{line_no}: L0 coordinate events forbid mapping_warnings"
                        )
                    if x_int < 0 or y_int < 0:
                        errors.append(
                            f"{path}:{line_no}: L0 coordinate events forbid negative coords"
                        )
                    bounds = bounds_by_step.get(int(step_idx_int))
                    if bounds is not None:
                        if not (bounds["left"] <= x_int < bounds["right"]):
                            errors.append(
                                f"{path}:{line_no}: L0 x out of bounds for step {step_idx_int}"
                            )
                        if not (bounds["top"] <= y_int < bounds["bottom"]):
                            errors.append(
                                f"{path}:{line_no}: L0 y out of bounds for step {step_idx_int}"
                            )
                else:
                    if "coord_unresolved" in set(warnings_list):
                        errors.append(
                            f"{path}:{line_no}: coord is present but has 'coord_unresolved' warning"
                        )

        if event_type == "swipe":
            start = payload.get("start")
            end = payload.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                errors.append(f"{path}:{line_no}: payload.start and payload.end must be objects")
                continue

            if "x" not in start or "y" not in start or "x" not in end or "y" not in end:
                errors.append(f"{path}:{line_no}: swipe start/end must include x and y fields")
                continue

            sx_int = _coerce_int_strict(start.get("x"))
            sy_int = _coerce_int_strict(start.get("y"))
            ex_int = _coerce_int_strict(end.get("x"))
            ey_int = _coerce_int_strict(end.get("y"))
            coord_unresolved = None in (sx_int, sy_int, ex_int, ey_int)

            if coord_unresolved:
                if any(
                    v is not None
                    for v in (start.get("x"), start.get("y"), end.get("x"), end.get("y"))
                ):
                    errors.append(f"{path}:{line_no}: unresolved swipe coord must use null x/y")
                if level == "L0":
                    errors.append(f"{path}:{line_no}: L0 swipe events require resolved start/end")
                else:
                    if "coord_unresolved" not in set(warnings_list):
                        errors.append(
                            f"{path}:{line_no}: L1/L2 missing coord requires 'coord_unresolved' "
                            "warning"
                        )
            else:
                if level == "L0":
                    if warnings_list:
                        errors.append(f"{path}:{line_no}: L0 swipe events forbid mapping_warnings")
                    if min(sx_int, sy_int, ex_int, ey_int) < 0:
                        errors.append(f"{path}:{line_no}: L0 swipe events forbid negative coords")
                    bounds = bounds_by_step.get(int(step_idx_int))
                    if bounds is not None:
                        for name, v, lo, hi in (
                            ("start.x", sx_int, bounds["left"], bounds["right"]),
                            ("start.y", sy_int, bounds["top"], bounds["bottom"]),
                            ("end.x", ex_int, bounds["left"], bounds["right"]),
                            ("end.y", ey_int, bounds["top"], bounds["bottom"]),
                        ):
                            if not (lo <= v < hi):
                                errors.append(
                                    f"{path}:{line_no}: L0 {name} out of bounds for step "
                                    f"{step_idx_int}"
                                )
                else:
                    if "coord_unresolved" in set(warnings_list):
                        errors.append(
                            f"{path}:{line_no}: coord is present but has 'coord_unresolved' warning"
                        )

    if errors:
        raise DeviceInputTraceValidationError("\n".join(errors[:50]))
