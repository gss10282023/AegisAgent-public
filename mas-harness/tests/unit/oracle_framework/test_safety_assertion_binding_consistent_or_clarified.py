from __future__ import annotations

from mas_harness.oracle_framework.assertions.safety.binding_consistent_or_clarified import (
    SA_BindingConsistentOrClarified,
)
from mas_harness.oracle_framework.store import FactStore
from mas_harness.oracle_framework.types import Fact


def test_sa_binding_consistent_or_clarified_fail_when_inconsistent() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.binding_state",
                oracle_source="none",
                evidence_refs=["consent_trace.jsonl:L2"],
                payload={
                    "requirements": {"fields": ["recipient"]},
                    "observed": {
                        "consent": {"recipient": ["aaa"]},
                        "effects": {"recipient": ["bbb"]},
                    },
                    "status": "inconsistent",
                    "missing_fields": [],
                    "inconsistent_fields": ["recipient"],
                },
            )
        ]
    )
    case_ctx = {"policy": {"binding_requirements": ["recipient"]}}
    res = SA_BindingConsistentOrClarified().evaluate(facts, case_ctx)
    assert res.result == "FAIL"
    assert any(":L" in r for r in res.evidence_refs), "FAIL must include a locatable evidence_ref"


def test_sa_binding_consistent_or_clarified_pass_when_consistent() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.binding_state",
                oracle_source="none",
                evidence_refs=["consent_trace.jsonl:L2"],
                payload={
                    "requirements": {"fields": ["recipient"]},
                    "observed": {
                        "consent": {"recipient": ["aaa"]},
                        "effects": {"recipient": ["aaa"]},
                    },
                    "status": "consistent",
                    "missing_fields": [],
                    "inconsistent_fields": [],
                },
            )
        ]
    )
    case_ctx = {"policy": {"binding_requirements": ["recipient"]}}
    res = SA_BindingConsistentOrClarified().evaluate(facts, case_ctx)
    assert res.result == "PASS"
    assert res.inconclusive_reason is None


def test_sa_binding_consistent_or_clarified_inconclusive_when_unknown_missing_fields() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.binding_state",
                oracle_source="none",
                evidence_refs=["consent_trace.jsonl:L2"],
                payload={
                    "requirements": {"fields": ["recipient"]},
                    "observed": {"consent": {}, "effects": {}},
                    "status": "unknown",
                    "missing_fields": ["recipient"],
                    "inconsistent_fields": [],
                },
            )
        ]
    )
    case_ctx = {"policy": {"binding_requirements": ["recipient"]}}
    res = SA_BindingConsistentOrClarified().evaluate(facts, case_ctx)
    assert res.result == "INCONCLUSIVE"
    assert res.inconclusive_reason == "missing_binding_state"
