from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Tuple


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(_json_dumps_canonical(obj) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(_json_dumps_canonical(obj))
        f.write("\n")
        f.flush()


def iter_jsonl(path: Path) -> Iterator[Tuple[int, Any]]:
    if not path.exists():
        return
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        yield i, json.loads(raw)
