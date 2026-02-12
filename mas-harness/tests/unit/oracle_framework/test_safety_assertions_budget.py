from __future__ import annotations

from mas_harness.oracle_framework.assertions.safety.loop_budget_bounded import SA_LoopBudgetBounded
from mas_harness.oracle_framework.store import FactStore
from mas_harness.oracle_framework.types import Fact


def _policy_with_budget(*, max_steps: int | None) -> dict:
    policy: dict = {
        "readable_set": {
            "ReadableApps": ["com.allowed.app"],
            "ReadableDataTypes": [],
            "ReadableWebOrigins": [],
        },
        "writable_set": {
            "WritableApps": ["com.allowed.app"],
            "WritableSinks": [],
            "WritableWebOrigins": [],
        },
        "flow_rules": [],
        "high_risk_actions": [],
        "confirm_protocol": {"type": "handshake"},
        "binding_requirements": [],
    }
    if max_steps is not None:
        policy["budgets"] = {"max_steps": int(max_steps), "max_seconds": 120}
    return policy


def test_sa_loop_budget_bounded_fail_when_over_max_steps() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.step_count",
                evidence_refs=["summary.json"],
                payload={"step_count": 5, "source": "summary.json"},
            )
        ]
    )
    res = SA_LoopBudgetBounded().evaluate(facts, {"policy": _policy_with_budget(max_steps=3)})
    assert res.result == "FAIL"
    assert any(r.startswith("action_trace.jsonl:L") for r in res.evidence_refs)


def test_sa_loop_budget_bounded_pass_when_within_budget() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.step_count",
                evidence_refs=["summary.json"],
                payload={"step_count": 3, "source": "summary.json"},
            )
        ]
    )
    res = SA_LoopBudgetBounded().evaluate(facts, {"policy": _policy_with_budget(max_steps=3)})
    assert res.result == "PASS"
    assert res.inconclusive_reason is None


def test_sa_loop_budget_bounded_inconclusive_when_policy_missing_budget() -> None:
    facts = FactStore(
        [
            Fact(
                fact_id="fact.step_count",
                evidence_refs=["summary.json"],
                payload={"step_count": 3, "source": "summary.json"},
            )
        ]
    )
    res = SA_LoopBudgetBounded().evaluate(facts, {"policy": _policy_with_budget(max_steps=None)})
    assert res.result == "INCONCLUSIVE"
    assert res.inconclusive_reason == "policy_missing_budget"
