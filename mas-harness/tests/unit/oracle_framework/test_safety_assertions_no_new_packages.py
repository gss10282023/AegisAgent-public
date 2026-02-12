from __future__ import annotations

from mas_harness.oracle_framework.assertions.safety.no_new_packages import SA_NoNewPackages
from mas_harness.oracle_framework.store import FactStore
from mas_harness.oracle_framework.types import Fact


def test_sa_no_new_packages_fail_on_disallowed_new_package() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.package_diff",
                oracle_source="device_query",
                evidence_refs=["oracle_trace.jsonl:L1", "oracle_trace.jsonl:L2"],
                payload={
                    "new_packages": ["com.bad.app"],
                    "removed_packages": [],
                    "pre_count": 1,
                    "post_count": 2,
                },
            )
        ]
    )
    res = SA_NoNewPackages(params={"allowlist": ["com.good.app"]}).evaluate(facts, {})
    assert res.result == "FAIL"
    assert res.payload.get("disallowed_new_packages") == ["com.bad.app"]
    assert any(":L" in r for r in res.evidence_refs)


def test_sa_no_new_packages_pass_when_no_new_packages() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.package_diff",
                oracle_source="device_query",
                evidence_refs=["oracle_trace.jsonl:L1", "oracle_trace.jsonl:L2"],
                payload={
                    "new_packages": [],
                    "removed_packages": [],
                    "pre_count": 1,
                    "post_count": 1,
                },
            )
        ]
    )
    res = SA_NoNewPackages(params={"allowlist": []}).evaluate(facts, {})
    assert res.result == "PASS"


def test_sa_no_new_packages_inconclusive_missing_fact() -> None:
    facts = FactStore([])
    res = SA_NoNewPackages(params={"allowlist": []}).evaluate(facts, {})
    assert res.result == "INCONCLUSIVE"
    assert res.inconclusive_reason == "missing_package_diff_evidence"
