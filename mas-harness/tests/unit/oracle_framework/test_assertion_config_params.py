from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mas_harness.oracle_framework import engine
from mas_harness.oracle_framework.assertion_base import Assertion
from mas_harness.oracle_framework.policy_compile import AssertionConfig
from mas_harness.oracle_framework.store import FactStore
from mas_harness.oracle_framework.types import AssertionResult


class DummyThresholdAssertion(Assertion):
    assertion_id = "DummyThresholdAssertion"
    required_fact_ids = ()

    def evaluate(self, facts: FactStore, case_ctx: Any) -> AssertionResult:
        _ = facts
        value = 0
        if isinstance(case_ctx, Mapping):
            raw = case_ctx.get("value")
            if isinstance(raw, int) and not isinstance(raw, bool):
                value = int(raw)
        threshold = 0
        raw_threshold = self.params.get("threshold")
        if isinstance(raw_threshold, int) and not isinstance(raw_threshold, bool):
            threshold = int(raw_threshold)

        payload = {"seen_params": dict(self.params), "value": value, "threshold": threshold}
        if value < threshold:
            return AssertionResult(
                assertion_id=self.assertion_id,
                result="FAIL",
                severity="low",
                evidence_refs=["dummy.txt:L1"],
                applicable=True,
                payload=payload,
            )
        return AssertionResult(
            assertion_id=self.assertion_id,
            result="PASS",
            severity="low",
            evidence_refs=[],
            applicable=True,
            payload=payload,
        )


def test_assertion_config_params_passthrough(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setitem(
        engine._ASSERTION_REGISTRY, DummyThresholdAssertion.assertion_id, DummyThresholdAssertion
    )

    # Legacy: string assertion_id (params=None).
    res = engine.run_assertions(
        tmp_path,
        {"value": 0},
        enabled_assertions=[DummyThresholdAssertion.assertion_id],
        facts=[],
    )
    assert len(res) == 1
    assert res[0].assertion_id == DummyThresholdAssertion.assertion_id
    assert res[0].result == "PASS"
    assert res[0].payload.get("seen_params") == {}

    # New: AssertionConfig(params=...) injected into the assertion instance.
    res = engine.run_assertions(
        tmp_path,
        {"value": 0},
        enabled_assertions=[
            AssertionConfig(
                assertion_id=DummyThresholdAssertion.assertion_id,
                params={"threshold": 1},
            )
        ],
        facts=[],
    )
    assert len(res) == 1
    assert res[0].result == "FAIL"
    assert res[0].payload.get("seen_params", {}).get("threshold") == 1
