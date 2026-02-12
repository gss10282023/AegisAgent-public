from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping


class CommProxyTraceParseError(ValueError):
    """Raised when a comm_proxy_trace JSONL line violates the input contract."""


_DIRECTION_VALUES = {"request", "response", "message"}


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


def _require_str(obj: Mapping[str, Any], key: str, *, source: str, line_no: int) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommProxyTraceParseError(f"{source}:{line_no}: '{key}' must be a non-empty string")
    return value.strip()


def iter_comm_proxy_trace_jsonl(
    lines: Iterable[str], *, source: str = "<memory>"
) -> Iterator[Dict[str, Any]]:
    """Yield validated comm_proxy_trace events from JSONL lines.

    Contract (Phase3 3b-5a minimal):
      - Required: timestamp_ms (int), direction (request/response/message), endpoint (str)
      - Required: payload OR payload_digest
      - Optional: status
    """

    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception as e:
            raise CommProxyTraceParseError(f"{source}:{line_no}: invalid json ({e})") from e

        if not isinstance(obj, dict):
            raise CommProxyTraceParseError(f"{source}:{line_no}: jsonl line must be an object")

        timestamp_ms = _coerce_int(obj.get("timestamp_ms"))
        if timestamp_ms is None:
            raise CommProxyTraceParseError(f"{source}:{line_no}: 'timestamp_ms' must be an int")

        direction = _require_str(obj, "direction", source=source, line_no=line_no).lower()
        if direction not in _DIRECTION_VALUES:
            raise CommProxyTraceParseError(
                f"{source}:{line_no}: 'direction' must be one of request/response/message"
            )

        endpoint = _require_str(obj, "endpoint", source=source, line_no=line_no)

        has_payload = "payload" in obj
        has_digest = "payload_digest" in obj
        if not (has_payload or has_digest):
            raise CommProxyTraceParseError(
                f"{source}:{line_no}: must include either 'payload' or 'payload_digest'"
            )

        normalized: Dict[str, Any] = dict(obj)
        normalized["timestamp_ms"] = timestamp_ms
        normalized["direction"] = direction
        normalized["endpoint"] = endpoint

        if has_digest:
            digest = obj.get("payload_digest")
            if not isinstance(digest, str) or not digest.strip():
                raise CommProxyTraceParseError(
                    f"{source}:{line_no}: 'payload_digest' must be a non-empty string"
                )
            normalized["payload_digest"] = digest.strip()

        if "status" in obj:
            status = obj.get("status")
            if status is not None and (
                isinstance(status, bool) or not isinstance(status, (int, str))
            ):
                raise CommProxyTraceParseError(
                    f"{source}:{line_no}: 'status' must be an int or string when provided"
                )
            if isinstance(status, str):
                normalized["status"] = status.strip()

        yield normalized


def load_comm_proxy_trace_jsonl(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return list(iter_comm_proxy_trace_jsonl(text.splitlines(), source=str(path)))
