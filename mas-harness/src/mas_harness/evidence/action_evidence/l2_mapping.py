from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence


class L2MappingError(ValueError):
    """Raised when comm_proxy_trace events cannot be mapped into device_input_trace(L2)."""


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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


def _extract_ref_step_idx(payload: Mapping[str, Any]) -> int | None:
    candidates = (
        "ref_step_idx",
        "action_step_idx",
        "agent_step_idx",
        "ref_step",
        "action_step",
    )
    for key in candidates:
        if key not in payload:
            continue
        idx = _coerce_int(payload.get(key))
        if idx is not None:
            return int(idx)
    return None


def _infer_evidence_dir(writer: Any) -> Path | None:
    path = getattr(writer, "path", None)
    if isinstance(path, Path):
        return path.parent

    paths = getattr(writer, "paths", None)
    root = getattr(paths, "root", None)
    if isinstance(root, Path):
        return root

    root = getattr(writer, "root", None)
    if isinstance(root, Path):
        return root

    return None


def _write_mapping_stats(*, evidence_dir: Path | None, stats: Mapping[str, Any]) -> None:
    if evidence_dir is None:
        return

    summary_path = evidence_dir / "summary.json"
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["action_evidence_mapping_stats"] = dict(stats)
        summary_path.write_text(_json_dumps_canonical(data) + "\n", encoding="utf-8")
        return

    stats_path = evidence_dir / "comm_proxy_mapping_stats.json"
    stats_path.write_text(_json_dumps_canonical(dict(stats)) + "\n", encoding="utf-8")


def materialize_l2_device_input_trace(
    comm_events: Sequence[Mapping[str, Any]],
    *,
    writer: Any,
    action_endpoint: str = "/act",
) -> dict[str, Any]:
    """Map comm_proxy_trace events into device_input_trace.jsonl via EvidenceWriter.

    Contract (Phase3 3b-5c):
      - source_level is "L2"
      - step_idx: 0..N-1 by action message order (monotonic & unique)
      - ref_step_idx: optional; may be null or repeated (1:N)
      - Non-action messages do not enter device_input_trace, but must be auditable via stats.
      - Coordinate events must end with coord_space="physical_px".
        Unresolved coords -> null + warning.
    """

    record = getattr(writer, "record_device_input_event", None)
    if not callable(record):
        raise L2MappingError(
            "writer must provide record_device_input_event(step_idx, ref_step_idx, ...)"
        )

    mapped_count = 0
    skipped_non_action_count = 0
    action_candidate_count = 0
    input_total_count = 0

    for msg_idx, event in enumerate(comm_events):
        input_total_count += 1
        if not isinstance(event, Mapping):
            raise L2MappingError(f"comm_events[{msg_idx}] must be an object")

        direction = str(event.get("direction") or "").strip().lower()
        endpoint = str(event.get("endpoint") or "").strip()

        is_action = direction == "request" and endpoint == action_endpoint
        if not is_action:
            skipped_non_action_count += 1
            continue

        action_candidate_count += 1

        ts = _coerce_int(event.get("timestamp_ms")) or 0
        payload_raw = event.get("payload")
        payload_in: Mapping[str, Any] | None = (
            payload_raw if isinstance(payload_raw, Mapping) else None
        )

        warnings: list[str] = []
        ref_step_idx: int | None = None
        if payload_in is None:
            warnings.append("payload_unavailable")
            raw_type = None
            payload_in = {}
        else:
            raw_type = payload_in.get("type")
            ref_step_idx = _extract_ref_step_idx(payload_in)

        event_type = _normalize_event_type(raw_type)

        payload_out: MutableMapping[str, Any]
        coord_space_raw = _normalize_coord_space(payload_in.get("coord_space"))

        if event_type in {"tap", "long_press"}:
            if coord_space_raw != "physical_px":
                warnings.append(f"coord_space_unconvertible:{coord_space_raw or 'unknown'}")
                warnings.append("coord_unresolved")
                payload_out = {"coord_space": "physical_px", "x": None, "y": None}
            else:
                x = payload_in.get("x") if "x" in payload_in else payload_in.get("x_px")
                y = payload_in.get("y") if "y" in payload_in else payload_in.get("y_px")
                x_int = _coerce_coord_int(x)
                y_int = _coerce_coord_int(y)
                if x_int is None or y_int is None:
                    warnings.append("coord_unresolved")
                    payload_out = {"coord_space": "physical_px", "x": None, "y": None}
                else:
                    payload_out = {"coord_space": "physical_px", "x": int(x_int), "y": int(y_int)}

        elif event_type == "swipe":
            if coord_space_raw != "physical_px":
                warnings.append(f"coord_space_unconvertible:{coord_space_raw or 'unknown'}")
                warnings.append("coord_unresolved")
                payload_out = {
                    "coord_space": "physical_px",
                    "start": {"x": None, "y": None},
                    "end": {"x": None, "y": None},
                }
            else:
                start = payload_in.get("start")
                end = payload_in.get("end")
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
                    payload_out = {
                        "coord_space": "physical_px",
                        "start": {"x": None, "y": None},
                        "end": {"x": None, "y": None},
                    }
                else:
                    payload_out = {
                        "coord_space": "physical_px",
                        "start": {"x": int(sx), "y": int(sy)},
                        "end": {"x": int(ex), "y": int(ey)},
                    }
                    duration_ms = _coerce_int(payload_in.get("duration_ms"))
                    if duration_ms is not None:
                        payload_out["duration_ms"] = int(max(0, duration_ms))

        elif event_type == "type":
            text_raw = payload_in.get("text")
            if isinstance(text_raw, str):
                text = text_raw
            else:
                text = "" if text_raw is None else str(text_raw)
            payload_out = {"text": text}

        elif event_type == "back":
            payload_out = {}

        elif event_type == "home":
            payload_out = {}

        elif event_type == "open_app":
            pkg = (
                payload_in.get("package")
                or payload_in.get("package_name")
                or payload_in.get("app_package")
            )
            pkg_str = str(pkg).strip() if pkg is not None else ""
            payload_out = {"package": pkg_str or None}

        elif event_type == "open_url":
            url = payload_in.get("url")
            url_str = str(url).strip() if url is not None else ""
            payload_out = {"url": url_str or None}

        elif event_type == "wait":
            duration_ms = _coerce_int(payload_in.get("duration_ms") or payload_in.get("ms"))
            payload_out = (
                {"duration_ms": int(max(0, duration_ms))} if duration_ms is not None else {}
            )

        elif event_type == "finished":
            payload_out = {}

        else:
            # No silent drop for action-shaped messages: map to a wait event with a warning.
            warnings.append(f"event_type_unsupported:{event_type}")
            payload_out = {"raw_type": str(raw_type) if raw_type is not None else None}
            event_type = "wait"

        payload_out.pop("ref_step_idx", None)

        record(
            mapped_count,
            ref_step_idx,
            "L2",
            event_type,
            dict(payload_out),
            ts,
            warnings,
        )
        mapped_count += 1

    stats = {
        "input_total_count": int(input_total_count),
        "action_candidate_count": int(action_candidate_count),
        "mapped_action_count": int(mapped_count),
        "skipped_non_action_count": int(skipped_non_action_count),
    }
    _write_mapping_stats(evidence_dir=_infer_evidence_dir(writer), stats=stats)
    return stats
