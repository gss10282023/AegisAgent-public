from __future__ import annotations

from typing import Any, Mapping, Sequence

from mas_harness.evidence.action_evidence.base import RawAgentEvent


class L1MappingError(ValueError):
    """Raised when raw events cannot be mapped into device_input_trace(L1)."""


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None
    return None


def _coerce_coord_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(round(float(value)))
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            try:
                return int(round(float(s)))
            except Exception:
                return None
    return None


def _normalize_coord_space(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "physical": "physical_px",
        "physicalpx": "physical_px",
        "screen_px": "screenshot_px",
        "screenpx": "screenshot_px",
        "screenshot": "screenshot_px",
        "px": "screenshot_px",
        "norm": "normalized_screenshot",
        "normalized_screen": "normalized_screenshot",
        "normalized_screenshot_px": "normalized_screenshot",
        "normalized_logical_px": "normalized_logical",
        "normalized_physical_px": "normalized_physical",
    }
    return aliases.get(s, s)


def _normalize_event_type(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return "unknown"
    s = raw.strip().lower()
    aliases = {
        "press_back": "back",
        "navigate_back": "back",
        "stop": "finished",
        "terminate": "finished",
        "done": "finished",
    }
    return aliases.get(s, s)


def _extract_ref_step_idx(event: Mapping[str, Any]) -> int | None:
    candidates = (
        "ref_step_idx",
        "action_step_idx",
        "agent_step_idx",
        "ref_step",
        "action_step",
    )
    for key in candidates:
        if key not in event:
            continue
        idx = _coerce_int(event.get(key))
        if idx is not None:
            return int(idx)
    return None


def _payload_for_coordinate_missing() -> dict[str, Any]:
    return {"coord_space": "physical_px", "x": None, "y": None}


def _payload_for_swipe_coordinate_missing() -> dict[str, Any]:
    return {
        "coord_space": "physical_px",
        "start": {"x": None, "y": None},
        "end": {"x": None, "y": None},
    }


def materialize_l1_device_input_trace(
    raw_events: Sequence[RawAgentEvent],
    *,
    writer: Any,
) -> None:
    """Map raw L1 agent events into device_input_trace.jsonl via EvidenceWriter.

    Contract (Phase3 3b-4c):
      - step_idx: event order index (0..N-1), strictly increasing and unique
      - ref_step_idx: optional, must be passed explicitly (never embedded in payload)
      - coord_space: always 'physical_px' for coordinate events; unresolved coords -> null + warning
      - no silent drop: unsupported event types are mapped to a 'wait' event with a warning
    """

    record = getattr(writer, "record_device_input_event", None)
    if not callable(record):
        raise L1MappingError(
            "writer must provide record_device_input_event(step_idx, ref_step_idx, ...)"
        )

    for event_idx, event in enumerate(raw_events):
        if not isinstance(event, Mapping):
            raise L1MappingError(f"raw_events[{event_idx}] must be an object")

        ts = _coerce_int(event.get("timestamp_ms"))
        if ts is None:
            ts = 0

        ref_step_idx = _extract_ref_step_idx(event)

        raw_type = event.get("type")
        event_type = _normalize_event_type(raw_type)
        warnings: list[str] = []

        payload: dict[str, Any]
        coord_space_raw = _normalize_coord_space(event.get("coord_space"))

        if event_type in {"tap", "long_press"}:
            if coord_space_raw not in {None, "physical_px"}:
                warnings.append(f"coord_space_unconvertible:{coord_space_raw}")
                warnings.append("coord_unresolved")
                payload = _payload_for_coordinate_missing()
            else:
                x = event.get("x") if "x" in event else event.get("x_px")
                y = event.get("y") if "y" in event else event.get("y_px")
                x_int = _coerce_coord_int(x)
                y_int = _coerce_coord_int(y)
                if x_int is None or y_int is None:
                    warnings.append("coord_unresolved")
                    payload = _payload_for_coordinate_missing()
                else:
                    payload = {"coord_space": "physical_px", "x": int(x_int), "y": int(y_int)}

        elif event_type == "swipe":
            if coord_space_raw not in {None, "physical_px"}:
                warnings.append(f"coord_space_unconvertible:{coord_space_raw}")
                warnings.append("coord_unresolved")
                payload = _payload_for_swipe_coordinate_missing()
            else:
                start = event.get("start")
                end = event.get("end")
                start_obj = dict(start) if isinstance(start, Mapping) else {}
                end_obj = dict(end) if isinstance(end, Mapping) else {}

                start_x = start_obj.get("x") if "x" in start_obj else start_obj.get("x_px")
                start_y = start_obj.get("y") if "y" in start_obj else start_obj.get("y_px")
                end_x = end_obj.get("x") if "x" in end_obj else end_obj.get("x_px")
                end_y = end_obj.get("y") if "y" in end_obj else end_obj.get("y_px")

                sx = _coerce_coord_int(start_x)
                sy = _coerce_coord_int(start_y)
                ex = _coerce_coord_int(end_x)
                ey = _coerce_coord_int(end_y)

                if None in (sx, sy, ex, ey):
                    warnings.append("coord_unresolved")
                    payload = _payload_for_swipe_coordinate_missing()
                else:
                    payload = {
                        "coord_space": "physical_px",
                        "start": {"x": int(sx), "y": int(sy)},
                        "end": {"x": int(ex), "y": int(ey)},
                    }
                    duration_ms = _coerce_int(event.get("duration_ms"))
                    if duration_ms is not None:
                        payload["duration_ms"] = int(max(0, duration_ms))

        elif event_type == "type":
            text_raw = event.get("text")
            if isinstance(text_raw, str):
                text = text_raw
            else:
                text = "" if text_raw is None else str(text_raw)
            payload = {"text": text}

        elif event_type == "back":
            payload = {}

        elif event_type == "home":
            payload = {}

        elif event_type == "open_app":
            pkg = event.get("package") or event.get("package_name") or event.get("app_package")
            pkg_str = str(pkg).strip() if pkg is not None else ""
            payload = {"package": pkg_str or None}

        elif event_type == "open_url":
            url = event.get("url")
            url_str = str(url).strip() if url is not None else ""
            payload = {"url": url_str or None}

        elif event_type == "wait":
            duration_ms = _coerce_int(event.get("duration_ms") or event.get("ms"))
            payload = {"duration_ms": int(max(0, duration_ms))} if duration_ms is not None else {}

        elif event_type == "finished":
            payload = {}

        else:
            # No silent drop: emit a wait event with an explicit warning.
            warnings.append(f"event_type_unsupported:{event_type}")
            payload = {"raw_type": str(raw_type) if raw_type is not None else None}
            event_type = "wait"

        # Must pass ref_step_idx explicitly; never store it inside payload.
        payload.pop("ref_step_idx", None)

        record(
            event_idx,
            ref_step_idx,
            "L1",
            event_type,
            payload,
            ts,
            warnings,
        )
