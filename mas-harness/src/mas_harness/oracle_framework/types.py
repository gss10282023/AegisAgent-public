from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence

FACT_SCHEMA_VERSION_V0 = "facts.v0"

ALLOWED_FACT_ORACLE_SOURCES_V0 = {"device_query", "trajectory_declared", "none"}

ALLOWED_ASSERTION_RESULTS_V0 = {"PASS", "FAIL", "INCONCLUSIVE"}

ALLOWED_SEVERITIES_V0 = {"low", "med", "high"}


@dataclass(frozen=True)
class Fact:
    fact_id: str
    schema_version: str = FACT_SCHEMA_VERSION_V0
    digest: str = ""
    oracle_source: str = "none"
    evidence_refs: Sequence[str] = field(default_factory=list)
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "schema_version": self.schema_version,
            "digest": self.digest,
            "oracle_source": self.oracle_source,
            "evidence_refs": list(self.evidence_refs),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class AssertionResult:
    assertion_id: str
    result: str
    evidence_refs: Sequence[str] = field(default_factory=list)
    payload: Mapping[str, Any] = field(default_factory=dict)

    severity: Optional[str] = None
    risk_weight_bucket: Optional[str] = None

    mapped_sp: Optional[str] = None
    mapped_primitive: Optional[str] = None
    mapped_boundary: Optional[str] = None
    impact_level: Optional[str] = None

    inconclusive_reason: Optional[str] = None
    applicable: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "result": self.result,
            "severity": self.severity,
            "risk_weight_bucket": self.risk_weight_bucket,
            "mapped_sp": self.mapped_sp,
            "mapped_primitive": self.mapped_primitive,
            "mapped_boundary": self.mapped_boundary,
            "impact_level": self.impact_level,
            "evidence_refs": list(self.evidence_refs),
            "payload": dict(self.payload),
            "inconclusive_reason": self.inconclusive_reason,
            "applicable": self.applicable,
        }
