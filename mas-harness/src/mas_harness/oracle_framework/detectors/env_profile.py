from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


class EnvProfileDetector(Detector):
    detector_id = "env_profile"
    produces_fact_ids = (
        "fact.env_profile",
        "fact.evidence_trust_level",
        "fact.oracle_source_summary",
    )

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        _ = case_ctx
        evidence_dir: Path = Path(getattr(pack, "evidence_dir", pack))
        run_manifest = getattr(pack, "run_manifest", None)

        summary_obj: dict[str, Any] = {}
        summary_path = evidence_dir / "summary.json"
        if summary_path.exists():
            try:
                obj = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                obj = None
            if isinstance(obj, dict):
                summary_obj = obj

        def _pick(key: str) -> Optional[str]:
            v = _nonempty_str(summary_obj.get(key))
            if v is not None:
                return v
            if isinstance(run_manifest, dict):
                return _nonempty_str(run_manifest.get(key))
            return None

        env_profile = _pick("env_profile")
        evidence_trust_level = _pick("evidence_trust_level")
        oracle_source_value = _pick("oracle_source")

        refs: list[str] = []
        if summary_path.exists():
            refs.append("summary.json")
        if isinstance(run_manifest, dict):
            refs.append("run_manifest.json")

        facts: list[Fact] = []
        if env_profile is not None:
            facts.append(
                Fact(
                    fact_id="fact.env_profile",
                    oracle_source="none",
                    evidence_refs=refs,
                    payload={"env_profile": env_profile},
                )
            )
        if evidence_trust_level is not None:
            facts.append(
                Fact(
                    fact_id="fact.evidence_trust_level",
                    oracle_source="none",
                    evidence_refs=refs,
                    payload={"evidence_trust_level": evidence_trust_level},
                )
            )
        if oracle_source_value is not None:
            facts.append(
                Fact(
                    fact_id="fact.oracle_source_summary",
                    oracle_source="none",
                    evidence_refs=refs,
                    payload={"oracle_source": oracle_source_value},
                )
            )
        return facts
