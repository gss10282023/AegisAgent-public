from __future__ import annotations

from mas_harness.oracle_framework.engine import compile_enabled_assertions


def _policy_with_budget(*, max_steps: int) -> dict:
    return {
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
        "budgets": {"max_steps": int(max_steps), "max_seconds": 120},
        "binding_requirements": [],
    }


def test_merge_semantics_baseline_eval_override_params_last_wins() -> None:
    policy = _policy_with_budget(max_steps=3)
    eval_spec = {
        "checkers_enabled": [
            {
                "assertion_id": "SA_ScopeForegroundApps",
                "params": {"allowlist": ["com.example.app"]},
            },
        ]
    }

    enabled, sources = compile_enabled_assertions(policy, eval_spec)
    by_id = {c.assertion_id: c for c in enabled}
    assert by_id["SA_ScopeForegroundApps"].params == {"allowlist": ["com.example.app"]}
    assert sources["SA_ScopeForegroundApps"] == "eval_override"


def test_merge_semantics_eval_can_disable_baseline_item() -> None:
    policy = _policy_with_budget(max_steps=3)
    eval_spec = {"checkers_enabled": [{"assertion_id": "SA_LoopBudgetBounded", "enabled": False}]}

    enabled, _sources = compile_enabled_assertions(policy, eval_spec)
    ids = [c.assertion_id for c in enabled]
    assert "SA_LoopBudgetBounded" not in ids
    assert "SA_ScopeForegroundApps" in ids, "baseline must not be disabled empty"


def test_merge_semantics_output_order_is_deterministic() -> None:
    policy = _policy_with_budget(max_steps=3)
    eval_spec = {
        "checkers_enabled": [
            "C4",
            {"assertion_id": "SA_ScopeForegroundApps", "params": {"k": "v"}},
        ]
    }

    enabled1, _sources1 = compile_enabled_assertions(policy, eval_spec)
    enabled2, _sources2 = compile_enabled_assertions(policy, eval_spec)
    assert [(c.assertion_id, c.params) for c in enabled1] == [
        (c.assertion_id, c.params) for c in enabled2
    ]

    ids = [c.assertion_id for c in enabled1]
    assert ids == sorted(ids)
