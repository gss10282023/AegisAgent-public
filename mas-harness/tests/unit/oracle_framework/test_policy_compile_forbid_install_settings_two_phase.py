from __future__ import annotations

from mas_harness.oracle_framework.policy_compile import compile_baseline_safety_assertions


def _base_policy() -> dict:
    return {
        "readable_set": {"ReadableApps": [], "ReadableDataTypes": [], "ReadableWebOrigins": []},
        "writable_set": {"WritableApps": [], "WritableSinks": [], "WritableWebOrigins": []},
        "flow_rules": [],
        "high_risk_actions": [],
        "confirm_protocol": {"type": "handshake"},
        "budgets": {"max_steps": 10, "max_seconds": 120},
        "binding_requirements": [],
    }


def test_policy_compile_v1_forbid_install_enables_sa_no_new_packages_with_allowlist() -> None:
    policy = _base_policy()
    policy["forbid_install"] = True
    policy["install_allowlist"] = ["com.example.allowed"]

    cfgs = compile_baseline_safety_assertions(policy)
    cfg_by_id = {c.assertion_id: c for c in cfgs}

    assert "SA_NoNewPackages" in cfg_by_id
    assert cfg_by_id["SA_NoNewPackages"].params.get("allowlist") == ["com.example.allowed"]
    assert [c.assertion_id for c in cfgs] == sorted(c.assertion_id for c in cfgs)


def test_policy_compile_v1_forbid_settings_change_enables_sa_no_settings_diff_with_fields() -> None:
    policy = _base_policy()
    policy["forbid_settings_change"] = {
        "fields": ["secure:location_mode", "global:airplane_mode_on"]
    }

    cfgs = compile_baseline_safety_assertions(policy)
    cfg_by_id = {c.assertion_id: c for c in cfgs}

    assert "SA_NoSettingsDiff" in cfg_by_id
    assert cfg_by_id["SA_NoSettingsDiff"].params.get("fields") == [
        "global:airplane_mode_on",
        "secure:location_mode",
    ]
    assert [c.assertion_id for c in cfgs] == sorted(c.assertion_id for c in cfgs)


def test_policy_compile_does_not_infer_install_or_settings_change_without_v1_fields() -> None:
    policy = _base_policy()
    policy.pop("forbid_install", None)
    policy.pop("forbid_settings_change", None)
    policy["allowed_actions"] = ["tap", "wait"]

    cfgs = compile_baseline_safety_assertions(policy)
    ids = [c.assertion_id for c in cfgs]
    assert "SA_NoNewPackages" not in ids
    assert "SA_NoSettingsDiff" not in ids
    assert ids == sorted(ids)
