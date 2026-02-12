#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _require_fields(obj: dict[str, Any], *, fields: list[str], where: str) -> None:
    missing = [k for k in fields if k not in obj]
    if missing:
        raise RuntimeError(f"{where}: missing fields: {missing}")


def _find_latest_results_json(*, runs_dir: Path) -> Path | None:
    candidates = list(runs_dir.glob("*/results.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _validate_episode(ep: dict[str, Any], *, idx: int) -> None:
    where = f"episodes[{idx}]"
    _require_fields(
        ep,
        fields=[
            "score",
            "max_score",
            "success",
            "time_used",
            "task_success",
            "attack_resistance",
            "efficiency",
        ],
        where=where,
    )

    if _as_float(ep["score"]) is None:
        raise RuntimeError(f"{where}.score must be a number")
    if _as_float(ep["max_score"]) is None:
        raise RuntimeError(f"{where}.max_score must be a number")
    if not isinstance(ep["success"], bool):
        raise RuntimeError(f"{where}.success must be a boolean")
    if _as_float(ep["time_used"]) is None:
        raise RuntimeError(f"{where}.time_used must be a number (seconds)")
    if not isinstance(ep["task_success"], bool):
        raise RuntimeError(f"{where}.task_success must be a boolean")
    if _as_float(ep["attack_resistance"]) is None:
        raise RuntimeError(f"{where}.attack_resistance must be a number")
    if _as_float(ep["efficiency"]) is None:
        raise RuntimeError(f"{where}.efficiency must be a number")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Validate Berkeley Phase 1 Step 7 fields in results.json "
            "(score/max_score/success/time_used + safety/efficiency dimensions)."
        )
    )
    ap.add_argument(
        "--results-path",
        type=str,
        default="",
        help="Path to results.json (defaults to latest under ./runs/*/results.json).",
    )
    ap.add_argument(
        "--runs-dir",
        type=str,
        default="runs",
        help="Directory containing run outputs (default: runs).",
    )
    args = ap.parse_args()

    results_path = Path(args.results_path).expanduser() if args.results_path else None
    if results_path is None:
        results_path = _find_latest_results_json(runs_dir=Path(args.runs_dir).expanduser())
    if results_path is None:
        raise RuntimeError("No results.json found (pass --results-path or generate a run first)")

    print(f"[step7] checking results_path={results_path}")
    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
        if not isinstance(results, dict):
            raise RuntimeError("results.json root must be an object")

        _require_fields(
            results,
            fields=[
                "score",
                "max_score",
                "success",
                "time_used",
                "task_success",
                "attack_resistance",
                "efficiency",
            ],
            where="root",
        )

        episodes = results.get("episodes")
        if not isinstance(episodes, list) or not episodes:
            raise RuntimeError("root.episodes must be a non-empty list")

        for idx, ep in enumerate(episodes):
            if not isinstance(ep, dict):
                raise RuntimeError(f"episodes[{idx}] must be an object")
            _validate_episode(ep, idx=idx)
    except Exception as exc:
        raise RuntimeError(f"{results_path}: {exc}") from exc

    print("[step7] OK: results.json includes Step 7 metrics/scoring fields")
    print(f"[step7] results_path={results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
