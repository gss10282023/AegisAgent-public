from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Tuple


class JsonlParseError(ValueError):
    """Raised when a JSONL line is invalid JSON or not an object."""


def iter_jsonl_objects(path: Path) -> Iterator[Tuple[int, dict[str, Any]]]:
    if not path.exists():
        return
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except Exception as e:
            raise JsonlParseError(f"{path}:{line_no}: invalid json ({e})") from e
        if not isinstance(obj, dict):
            raise JsonlParseError(f"{path}:{line_no}: jsonl line must be an object")
        yield line_no, obj
