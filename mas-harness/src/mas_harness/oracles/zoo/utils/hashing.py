"""Hashing helpers for Oracle Zoo.

Phase 2 Step 1: OracleEvidence v0 requires stable digests over structured results.
These helpers provide deterministic JSON encoding + SHA-256 hashing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def json_dumps_canonical(obj: Any) -> str:
    """Deterministic JSON encoding for hashing / stable digests."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_sha256(obj: Any) -> str:
    """Compute a stable SHA-256 digest for arbitrary JSON-serializable objects."""

    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)
    else:
        data = json_dumps_canonical(obj).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def stable_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
