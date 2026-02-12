from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.detectors._jsonl import iter_jsonl_objects
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


class ActionEvidenceDetector(Detector):
    detector_id = "action_evidence"
    produces_fact_ids = ("fact.action_trace_level", "fact.action_trace_source")

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        _ = case_ctx
        evidence_dir: Path = Path(getattr(pack, "evidence_dir", pack))
        run_manifest = getattr(pack, "run_manifest", None)
        if not isinstance(run_manifest, dict):
            return []

        level = _nonempty_str(run_manifest.get("action_trace_level")) or "none"
        source = _nonempty_str(run_manifest.get("action_trace_source")) or "none"

        device_input_path = evidence_dir / "device_input_trace.jsonl"
        observed_levels: set[str] = set()
        event_count = 0
        refs: list[str] = ["run_manifest.json"]
        if device_input_path.exists():
            refs.append(device_input_path.name)
            for line_no, obj in iter_jsonl_objects(device_input_path):
                lvl = _nonempty_str(obj.get("source_level"))
                if lvl is None:
                    continue
                observed_levels.add(lvl)
                event_count += 1
                if event_count <= 3:
                    refs.append(f"{device_input_path.name}:L{line_no}")

        payload_common = {
            "device_input_event_count": int(event_count),
            "observed_source_levels": sorted(observed_levels),
        }

        return [
            Fact(
                fact_id="fact.action_trace_level",
                oracle_source="none",
                evidence_refs=refs,
                payload={"action_trace_level": level, **payload_common},
            ),
            Fact(
                fact_id="fact.action_trace_source",
                oracle_source="none",
                evidence_refs=refs,
                payload={"action_trace_source": source, **payload_common},
            ),
        ]
