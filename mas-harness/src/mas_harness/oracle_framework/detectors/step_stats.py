from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.types import Fact


def _safe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None
    return None


class StepStatsDetector(Detector):
    detector_id = "step_stats"
    evidence_required = ("summary.json",)
    produces_fact_ids = ("fact.step_count",)

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        _ = case_ctx
        evidence_dir: Path = Path(getattr(pack, "evidence_dir", pack))
        summary_path = evidence_dir / "summary.json"
        if not summary_path.exists():
            return []

        try:
            summary_obj = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(summary_obj, dict):
            return []

        facts: list[Fact] = []

        steps = _safe_int(summary_obj.get("steps"))
        if steps is None:
            steps = _safe_int(summary_obj.get("steps_executed"))
        if steps is None:
            steps = _safe_int(summary_obj.get("step_count"))

        source = "summary.json"
        if steps is None:
            action_trace = evidence_dir / "action_trace.jsonl"
            if action_trace.exists():
                try:
                    steps = sum(
                        1
                        for line in action_trace.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    )
                    source = "action_trace.jsonl"
                except Exception:
                    steps = None

        if steps is not None:
            facts.append(
                Fact(
                    fact_id="fact.step_count",
                    oracle_source="none",
                    evidence_refs=[source],
                    payload={"step_count": int(steps), "source": source},
                )
            )

        return facts
