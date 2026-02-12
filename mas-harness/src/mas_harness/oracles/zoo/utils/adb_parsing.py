"""ADB parsing helpers for Oracle Zoo.

This module exists because `adb shell content query` emits a *human-readable*
format that is easy to mis-parse:

  Row: 0 _id=5, thread_id=5, body=Hello, World, read=1

Values may contain commas/spaces (e.g. SMS body), so naive `split(", ")` breaks.

We provide a parser that:
  - splits rows reliably (even if values contain newlines)
  - parses key/value pairs using *expected keys* when available (preferred)
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Dict, List, Optional

_NO_RESULT_PREFIXES = (
    "No result found.",
    "No result found",
)

_ROW_HEADER_RE = re.compile(r"^Row:\s*(?P<row>\d+)\s*", flags=re.IGNORECASE)
_ROW_START_RE = re.compile(r"^Row:\s*\d+", flags=re.IGNORECASE | re.MULTILINE)


def is_content_query_no_result(stdout: str) -> bool:
    s = (stdout or "").strip()
    return any(s.startswith(p) for p in _NO_RESULT_PREFIXES)


def split_content_query_rows(stdout: str) -> List[str]:
    """Split `content query` stdout into per-row strings.

    Android's `content` tool prints each row prefixed by `Row: N`. Values can
    contain commas/spaces and (rarely) newlines; splitting via `\n` or `, ` is
    not safe.
    """

    txt = (stdout or "").replace("\r", "")
    if not txt.strip() or is_content_query_no_result(txt):
        return []

    matches = list(_ROW_START_RE.finditer(txt))
    if not matches:
        return []

    rows: List[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
        chunk = txt[start:end].strip()
        if chunk:
            rows.append(chunk)
    return rows


def _parse_known_keys(payload: str, *, expected_keys: Sequence[str]) -> Dict[str, str]:
    keys = [str(k) for k in expected_keys if str(k)]
    if not keys:
        return {}

    # Only treat `, <expected_key>=` as a delimiter. This prevents values that
    # contain commas/spaces (and even `x=y`) from confusing the parser.
    union = "|".join(re.escape(k) for k in keys)
    key_re = re.compile(rf"(?:^|, )(?P<key>{union})=")
    matches = list(key_re.finditer(payload))
    if not matches:
        return {}

    out: Dict[str, str] = {}
    for i, m in enumerate(matches):
        key = m.group("key")
        value_start = m.end()
        value_end = matches[i + 1].start() if i + 1 < len(matches) else len(payload)
        out[key] = payload[value_start:value_end].strip()
    return out


_GENERIC_KV_RE = re.compile(r"(?:^|, )(?P<key>[^=,]+)=(?P<value>.*?)(?=, [^=,]+=|$)", re.DOTALL)


def _parse_generic(payload: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _GENERIC_KV_RE.finditer(payload):
        key = (m.group("key") or "").strip()
        if not key:
            continue
        out[key] = (m.group("value") or "").strip()
    return out


def parse_content_query_row(
    row: str,
    *,
    expected_keys: Optional[Sequence[str]] = None,
    include_row_index: bool = True,
) -> Dict[str, Any]:
    """Parse a single `Row: N ...` line into a dict.

    If expected_keys is provided, parsing is significantly more robust.
    """

    line = (row or "").strip()
    if not line:
        return {}

    row_index: Optional[int] = None
    m = _ROW_HEADER_RE.match(line)
    payload = line
    if m:
        try:
            row_index = int(m.group("row"))
        except Exception:
            row_index = None
        payload = line[m.end() :].strip()

    if expected_keys:
        parsed = _parse_known_keys(payload, expected_keys=expected_keys)
        if not parsed:
            parsed = _parse_generic(payload)
    else:
        parsed = _parse_generic(payload)

    if include_row_index and row_index is not None:
        parsed["_row"] = row_index
    return parsed


def parse_content_query_output(
    stdout: str,
    *,
    expected_keys: Optional[Sequence[str]] = None,
    include_row_index: bool = True,
) -> List[Dict[str, Any]]:
    """Parse full `content query` stdout into list[dict]."""

    rows = split_content_query_rows(stdout)
    parsed: List[Dict[str, Any]] = []
    for row in rows:
        item = parse_content_query_row(
            row,
            expected_keys=expected_keys,
            include_row_index=include_row_index,
        )
        if item:
            parsed.append(item)
    return parsed
