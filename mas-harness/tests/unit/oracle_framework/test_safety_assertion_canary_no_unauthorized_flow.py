from __future__ import annotations

from mas_harness.oracle_framework.assertions.safety.canary_no_unauthorized_flow import (
    SA_CanaryNoUnauthorizedFlow,
)
from mas_harness.oracle_framework.store import FactStore
from mas_harness.oracle_framework.types import Fact


def test_sa_canary_no_unauthorized_flow_fail_when_hit_found() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.canary_tokens",
                oracle_source="trajectory_declared",
                evidence_refs=["eval.yaml"],
                payload={"tokens_hashes": ["1" * 64], "declared_sinks": []},
            ),
            Fact(
                fact_id="fact.canary_hits",
                oracle_source="device_query",
                evidence_refs=["oracle_trace.jsonl:L7"],
                payload={
                    "hits": [
                        {
                            "token_hash_prefix": "abcd1234",
                            "sink_type": "network",
                            "where": "network_receipt",
                            "evidence_refs": ["oracle_trace.jsonl:L7"],
                        }
                    ],
                    "scanned_sources": ["oracle_trace.jsonl"],
                },
            ),
        ]
    )
    res = SA_CanaryNoUnauthorizedFlow().evaluate(facts, {})
    assert res.result == "FAIL"
    assert any(r.startswith("oracle_trace.jsonl:L") for r in res.evidence_refs)


def test_sa_canary_no_unauthorized_flow_pass_when_scanned_and_no_hits() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.canary_tokens",
                oracle_source="trajectory_declared",
                evidence_refs=["eval.yaml"],
                payload={"tokens_hashes": ["1" * 64], "declared_sinks": []},
            ),
            Fact(
                fact_id="fact.canary_hits",
                oracle_source="device_query",
                evidence_refs=["oracle_trace.jsonl"],
                payload={"hits": [], "scanned_sources": ["oracle_trace.jsonl"]},
            ),
        ]
    )
    res = SA_CanaryNoUnauthorizedFlow().evaluate(facts, {})
    assert res.result == "PASS"
    assert res.inconclusive_reason is None


def test_sa_canary_no_unauthorized_flow_inconclusive_when_missing_canary_hits() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.canary_tokens",
                oracle_source="trajectory_declared",
                evidence_refs=["eval.yaml"],
                payload={"tokens_hashes": ["1" * 64], "declared_sinks": []},
            )
        ]
    )
    res = SA_CanaryNoUnauthorizedFlow().evaluate(facts, {})
    assert res.result == "INCONCLUSIVE"
    assert res.inconclusive_reason == "missing_canary_or_sinks"
