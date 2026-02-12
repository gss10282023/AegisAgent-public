from __future__ import annotations

import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / "mas-harness" / "src"
    src_str = str(src)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)

    # Some unit test helpers live under `tests/unit/...` (e.g., oracle_zoo fakes).
    oracles_tests_root = Path(__file__).resolve().parent / "unit" / "oracles"
    oracles_tests_root_str = str(oracles_tests_root)
    if oracles_tests_root.is_dir() and oracles_tests_root_str not in sys.path:
        sys.path.insert(0, oracles_tests_root_str)


_ensure_src_on_path()
