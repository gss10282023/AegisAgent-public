from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mas_harness.evidence import stable_sha256
from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.detectors._jsonl import iter_jsonl_objects
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


class OracleEventIndexDetector(Detector):
    detector_id = "oracle_event_index"
    evidence_required = ("oracle_trace.jsonl",)
    produces_fact_ids = ()

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        _ = case_ctx
        evidence_dir: Path = Path(getattr(pack, "evidence_dir", pack))
        path = evidence_dir / "oracle_trace.jsonl"
        if not path.exists():
            return []

        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        refs_by_group: dict[tuple[str, str], set[str]] = {}

        for line_no, obj in iter_jsonl_objects(path):
            oracle_name = _nonempty_str(obj.get("oracle_name"))
            phase = _nonempty_str(obj.get("phase"))
            if oracle_name is None or phase is None:
                continue

            key = (oracle_name, phase)
            groups.setdefault(key, [])
            refs_by_group.setdefault(key, set()).add(f"{path.name}:L{line_no}")

            artifacts = obj.get("artifacts")
            artifact_paths: list[str] = []
            if isinstance(artifacts, list):
                for a in artifacts:
                    if not isinstance(a, dict):
                        continue
                    p = _nonempty_str(a.get("path"))
                    if p is not None:
                        artifact_paths.append(p)
                        refs_by_group[key].add(p)

            event_summary = {
                "line": int(line_no),
                "oracle_id": _nonempty_str(obj.get("oracle_id")),
                "oracle_type": _nonempty_str(obj.get("oracle_type")),
                "result_digest": _nonempty_str(obj.get("result_digest")),
                "result_preview": obj.get("result_preview"),
                "anti_gaming_notes": obj.get("anti_gaming_notes"),
                "decision": obj.get("decision"),
                "queries_count": len(obj.get("queries"))
                if isinstance(obj.get("queries"), list)
                else None,
                "queries_digest": (
                    stable_sha256(obj.get("queries"))
                    if isinstance(obj.get("queries"), list) and obj.get("queries")
                    else None
                ),
                "artifact_paths": artifact_paths or None,
            }
            groups[key].append(event_summary)

        facts: list[Fact] = []
        for (oracle_name, phase), events in sorted(
            groups.items(), key=lambda kv: (kv[0][0], kv[0][1])
        ):
            refs = sorted(refs_by_group.get((oracle_name, phase), set()) | {path.name})
            payload = {
                "oracle_name": oracle_name,
                "phase": phase,
                "event_count": len(events),
                "events": events,
            }
            facts.append(
                Fact(
                    fact_id=f"fact.oracle_event_index/{oracle_name}/{phase}",
                    oracle_source="device_query",
                    evidence_refs=refs,
                    payload=payload,
                )
            )
        return facts
