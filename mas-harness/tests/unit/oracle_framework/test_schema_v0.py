from __future__ import annotations

import pytest

from mas_harness.oracle_framework.schema_validators import (
    assert_assertion_result_v0,
    assert_fact_v0,
)
from mas_harness.oracle_framework.types import (
    FACT_SCHEMA_VERSION_V0,
    AssertionResult,
    Fact,
)


def test_fact_v0_minimal_valid() -> None:
    fact = {
        "fact_id": "fact.example",
        "schema_version": FACT_SCHEMA_VERSION_V0,
        "digest": "0" * 64,
        "oracle_source": "none",
        "evidence_refs": [],
        "payload": {},
    }
    assert_fact_v0(fact)

    fact_dc = Fact(
        fact_id="fact.example",
        schema_version=FACT_SCHEMA_VERSION_V0,
        digest="0" * 64,
        oracle_source="none",
        evidence_refs=[],
        payload={},
    )
    assert_fact_v0(fact_dc.to_dict())


@pytest.mark.parametrize(
    "patch",
    [
        {"fact_id": ""},
        {"schema_version": "facts.v999"},
        {"digest": "not-a-digest"},
        {"oracle_source": "adb"},
        {"evidence_refs": [123]},
        {"payload": "nope"},
    ],
)
def test_fact_v0_invalid_rejected(patch: dict) -> None:
    base = {
        "fact_id": "fact.example",
        "schema_version": FACT_SCHEMA_VERSION_V0,
        "digest": "0" * 64,
        "oracle_source": "none",
        "evidence_refs": [],
        "payload": {},
    }
    base.update(patch)
    with pytest.raises(ValueError):
        assert_fact_v0(base)


def test_assertion_result_v0_pass_fail_inconclusive_valid() -> None:
    pass_result = {
        "assertion_id": "assert.example.pass",
        "result": "PASS",
        "severity": "low",
        "mapped_sp": None,
        "mapped_primitive": None,
        "mapped_boundary": None,
        "impact_level": None,
        "evidence_refs": [],
    }
    assert_assertion_result_v0(pass_result)

    fail_result = {
        "assertion_id": "assert.example.fail",
        "result": "FAIL",
        "risk_weight_bucket": "high",
        "mapped_sp": None,
        "mapped_primitive": None,
        "mapped_boundary": None,
        "impact_level": None,
        "evidence_refs": [],
    }
    assert_assertion_result_v0(fail_result)

    inconclusive = AssertionResult(
        assertion_id="assert.example.inconclusive",
        result="INCONCLUSIVE",
        severity="med",
        inconclusive_reason="missing evidence",
    )
    assert_assertion_result_v0(inconclusive.to_dict())


def test_assertion_result_v0_inconclusive_requires_reason() -> None:
    missing_reason = {
        "assertion_id": "assert.example",
        "result": "INCONCLUSIVE",
        "severity": "low",
        "mapped_sp": None,
        "mapped_primitive": None,
        "mapped_boundary": None,
        "impact_level": None,
        "evidence_refs": [],
    }
    with pytest.raises(ValueError):
        assert_assertion_result_v0(missing_reason)


@pytest.mark.parametrize(
    "patch",
    [
        {"result": "UNKNOWN"},
        {"severity": "critical"},
        {"severity": None, "risk_weight_bucket": None},
        {"mapped_sp": "__DELETE__"},
        {"evidence_refs": [None]},
    ],
)
def test_assertion_result_v0_invalid_rejected(patch: dict) -> None:
    base = {
        "assertion_id": "assert.example",
        "result": "PASS",
        "severity": "low",
        "mapped_sp": None,
        "mapped_primitive": None,
        "mapped_boundary": None,
        "impact_level": None,
        "evidence_refs": [],
    }

    patch = dict(patch)
    delete_keys = {k for k, v in patch.items() if v == "__DELETE__"}
    for k in delete_keys:
        patch.pop(k, None)
        base.pop(k, None)

    base.update(patch)
    with pytest.raises(ValueError):
        assert_assertion_result_v0(base)
