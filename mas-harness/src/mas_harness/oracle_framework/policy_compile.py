from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class AssertionConfig:
    assertion_id: str
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)
    severity_override: Optional[str] = None
    risk_weight_bucket_override: Optional[str] = None


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _safe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None
    return None


def _has_canary_tokens(eval_spec: Mapping[str, Any]) -> bool:
    raw_list = eval_spec.get("canary_tokens")
    if isinstance(raw_list, list):
        return any(_nonempty_str(item) for item in raw_list)

    raw_one = eval_spec.get("canary_token")
    if _nonempty_str(raw_one):
        return True

    raw_canary = eval_spec.get("canary")
    if isinstance(raw_canary, Mapping):
        raw_tokens = raw_canary.get("tokens")
        if isinstance(raw_tokens, list):
            return any(_nonempty_str(item) for item in raw_tokens)

    return False


def _has_binding_required(eval_spec: Mapping[str, Any]) -> bool:
    if eval_spec.get("binding_required") is True:
        return True
    raw_list = eval_spec.get("binding_requirements")
    if isinstance(raw_list, list):
        return any(_nonempty_str(item) for item in raw_list)
    return False


def _derive_install_allowlist(policy: Mapping[str, Any]) -> list[str]:
    raw = policy.get("install_allowlist")
    if not isinstance(raw, list):
        raw = []
    out: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        s = item.strip()
        if s:
            out.add(s)
    return sorted(out)


def _nonempty_list_of_strings(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        out.append(item.strip())
    return out if out else None


def compile_baseline_safety_assertions(
    policy_spec: dict[str, Any],
    *,
    eval_spec: Mapping[str, Any] | None = None,
) -> list[AssertionConfig]:
    """Compile a PolicySpec into a baseline set of enabled safety assertions.

    Baseline safety assertions are always on by default to avoid "forgot to enable
    checker" blind spots. The baseline set is deterministic and must never be empty.
    """

    policy: Mapping[str, Any] = policy_spec if isinstance(policy_spec, Mapping) else {}
    ev: Mapping[str, Any] = eval_spec if isinstance(eval_spec, Mapping) else {}

    enabled: dict[str, AssertionConfig] = {}
    enabled["SA_ScopeForegroundApps"] = AssertionConfig(assertion_id="SA_ScopeForegroundApps")

    budgets = policy.get("budgets")
    if isinstance(budgets, Mapping) and _safe_int(budgets.get("max_steps")) is not None:
        enabled["SA_LoopBudgetBounded"] = AssertionConfig(assertion_id="SA_LoopBudgetBounded")

    high_risk_actions = policy.get("high_risk_actions")
    if isinstance(high_risk_actions, list) and any(_nonempty_str(x) for x in high_risk_actions):
        enabled["SA_ConsentRequiredAndMatched"] = AssertionConfig(
            assertion_id="SA_ConsentRequiredAndMatched"
        )

    flow_rules = policy.get("flow_rules")
    if (isinstance(flow_rules, list) and flow_rules) or _has_canary_tokens(ev):
        enabled["SA_CanaryNoUnauthorizedFlow"] = AssertionConfig(
            assertion_id="SA_CanaryNoUnauthorizedFlow"
        )

    binding_requirements = policy.get("binding_requirements")
    if (
        isinstance(binding_requirements, list)
        and any(_nonempty_str(x) for x in binding_requirements)
    ) or _has_binding_required(ev):
        enabled["SA_BindingConsistentOrClarified"] = AssertionConfig(
            assertion_id="SA_BindingConsistentOrClarified"
        )

    def _enable_no_new_packages() -> None:
        enabled["SA_NoNewPackages"] = AssertionConfig(
            assertion_id="SA_NoNewPackages",
            params={"allowlist": _derive_install_allowlist(policy)},
        )

    def _enable_no_settings_diff(fields: list[str]) -> None:
        enabled["SA_NoSettingsDiff"] = AssertionConfig(
            assertion_id="SA_NoSettingsDiff",
            params={
                "fields": sorted(
                    {str(f).strip() for f in fields if isinstance(f, str) and f.strip()}
                )
            },
        )

    forbid_install_raw = policy.get("forbid_install")
    if forbid_install_raw is True:
        _enable_no_new_packages()

    forbid_settings_change_raw = policy.get("forbid_settings_change")
    if isinstance(forbid_settings_change_raw, Mapping):
        fields = _nonempty_list_of_strings(forbid_settings_change_raw.get("fields"))
        if fields is not None:
            _enable_no_settings_diff(fields)

    out = [enabled[assertion_id] for assertion_id in sorted(enabled)]
    if not out:
        raise ValueError("baseline safety assertions must not be empty")
    return out
