from __future__ import annotations

from pathlib import Path

from mas_harness.oracle_framework.policy_compile import compile_baseline_safety_assertions
from mas_harness.spec.spec_loader import load_yaml_or_json


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


def test_policy_compile_smoke_001_includes_scope_and_budget() -> None:
    cfgs = compile_baseline_safety_assertions(_policy_with_budget(max_steps=3))
    ids = [c.assertion_id for c in cfgs]
    assert ids == sorted(ids), "compiler must be deterministic (sorted output)"
    assert "SA_ScopeForegroundApps" in ids
    assert "SA_LoopBudgetBounded" in ids


def test_policy_compile_excludes_budget_when_missing_max_steps() -> None:
    cfgs = compile_baseline_safety_assertions(_policy_with_budget(max_steps=None))
    ids = [c.assertion_id for c in cfgs]
    assert "SA_ScopeForegroundApps" in ids
    assert "SA_LoopBudgetBounded" not in ids


def test_policy_compile_public_cases_nonempty_and_contains_scope() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    cases_root = repo_root / "mas-public" / "cases"
    assert cases_root.is_dir(), f"missing cases dir: {cases_root}"

    policy_paths = sorted(cases_root.rglob("policy.yaml"))
    assert policy_paths, "expected at least one policy.yaml in mas-public/cases"

    for policy_path in policy_paths:
        policy = load_yaml_or_json(policy_path)
        cfgs = compile_baseline_safety_assertions(policy)
        ids = [c.assertion_id for c in cfgs]
        assert ids, f"expected non-empty baseline assertions for {policy_path}"
        assert "SA_ScopeForegroundApps" in ids, f"expected scope assertion for {policy_path}"
