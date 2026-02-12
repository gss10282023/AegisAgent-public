"""Import shim for the src/ layout.

This repo keeps the real package under `mas-harness/src/mas_harness/`.
When running CLIs directly from the repo root (e.g. `python -m mas_harness...`),
Python won't find that path unless PYTHONPATH is set.

This shim makes the repo root runnable without extra env configuration by
extending the package search path to include the src directory.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REAL_PKG = _REPO_ROOT / "mas-harness" / "src" / "mas_harness"
if _REAL_PKG.is_dir():
    __path__.append(str(_REAL_PKG))  # type: ignore[name-defined]

__all__ = [
    "cli",
    "evidence",
    "examples",
    "integration",
    "oracles",
    "phases",
    "reporting",
    "runtime",
    "spec",
    "tools",
]
