from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping


class AgentEventsV1ParseError(ValueError):
    """Raised when an agent_events_v1 JSONL line violates the input contract."""


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


def _is_number_or_none(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_str(obj: Mapping[str, Any], key: str, *, source: str, line_no: int) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AgentEventsV1ParseError(f"{source}:{line_no}: '{key}' must be a non-empty string")
    return value.strip()


def _require_coord_space(obj: Mapping[str, Any], *, source: str, line_no: int) -> str:
    coord_space = obj.get("coord_space")
    if not isinstance(coord_space, str) or not coord_space.strip():
        raise AgentEventsV1ParseError(
            f"{source}:{line_no}: 'coord_space' must be provided when coordinates are present"
        )
    return coord_space.strip().lower()


def iter_agent_events_v1_jsonl(
    lines: Iterable[str], *, source: str = "<memory>"
) -> Iterator[Dict[str, Any]]:
    """Yield validated agent_events_v1 events from JSONL lines.

    Contract (Phase3 3b-4a minimal):
      - Required: timestamp_ms (int), type (str)
      - Optional coordinates:
          * coord_space + x/y
          * coord_space + start/end where start/end are objects containing x/y
    """

    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception as e:
            raise AgentEventsV1ParseError(f"{source}:{line_no}: invalid json ({e})") from e

        if not isinstance(obj, dict):
            raise AgentEventsV1ParseError(f"{source}:{line_no}: jsonl line must be an object")

        timestamp_ms = _coerce_int(obj.get("timestamp_ms"))
        if timestamp_ms is None:
            raise AgentEventsV1ParseError(f"{source}:{line_no}: 'timestamp_ms' must be an int")

        event_type = _require_str(obj, "type", source=source, line_no=line_no).lower()

        has_xy = "x" in obj or "y" in obj
        has_start_end = "start" in obj or "end" in obj
        if has_xy and has_start_end:
            raise AgentEventsV1ParseError(
                f"{source}:{line_no}: cannot have both x/y and start/end coordinates"
            )

        normalized: Dict[str, Any] = dict(obj)
        normalized["timestamp_ms"] = timestamp_ms
        normalized["type"] = event_type

        if has_xy:
            coord_space = _require_coord_space(obj, source=source, line_no=line_no)
            if "x" not in obj or "y" not in obj:
                raise AgentEventsV1ParseError(
                    f"{source}:{line_no}: both 'x' and 'y' must be provided when using x/y "
                    "coordinates"
                )
            x = obj.get("x")
            y = obj.get("y")
            if not _is_number_or_none(x) or not _is_number_or_none(y):
                raise AgentEventsV1ParseError(
                    f"{source}:{line_no}: 'x'/'y' must be numbers (or null)"
                )
            normalized["coord_space"] = coord_space

        if has_start_end:
            coord_space = _require_coord_space(obj, source=source, line_no=line_no)
            start_obj = obj.get("start")
            end_obj = obj.get("end")
            if not isinstance(start_obj, dict) or not isinstance(end_obj, dict):
                raise AgentEventsV1ParseError(
                    f"{source}:{line_no}: 'start' and 'end' must be objects when present"
                )
            if "x" not in start_obj or "y" not in start_obj:
                raise AgentEventsV1ParseError(
                    f"{source}:{line_no}: 'start' must include 'x' and 'y'"
                )
            if "x" not in end_obj or "y" not in end_obj:
                raise AgentEventsV1ParseError(f"{source}:{line_no}: 'end' must include 'x' and 'y'")
            if not _is_number_or_none(start_obj.get("x")) or not _is_number_or_none(
                start_obj.get("y")
            ):
                raise AgentEventsV1ParseError(
                    f"{source}:{line_no}: 'start.x'/'start.y' must be numbers (or null)"
                )
            if not _is_number_or_none(end_obj.get("x")) or not _is_number_or_none(end_obj.get("y")):
                raise AgentEventsV1ParseError(
                    f"{source}:{line_no}: 'end.x'/'end.y' must be numbers (or null)"
                )

            normalized["coord_space"] = coord_space

        yield normalized


def load_agent_events_v1_jsonl(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return list(iter_agent_events_v1_jsonl(text.splitlines(), source=str(path)))
