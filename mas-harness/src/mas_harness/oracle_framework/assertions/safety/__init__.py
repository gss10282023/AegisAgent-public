from __future__ import annotations

from mas_harness.oracle_framework.assertions.safety.binding_consistent_or_clarified import (
    SA_BindingConsistentOrClarified,
)
from mas_harness.oracle_framework.assertions.safety.canary_no_unauthorized_flow import (
    SA_CanaryNoUnauthorizedFlow,
)
from mas_harness.oracle_framework.assertions.safety.consent_required_and_matched import (
    SA_ConsentRequiredAndMatched,
)
from mas_harness.oracle_framework.assertions.safety.loop_budget_bounded import SA_LoopBudgetBounded
from mas_harness.oracle_framework.assertions.safety.no_new_packages import SA_NoNewPackages
from mas_harness.oracle_framework.assertions.safety.no_settings_diff import SA_NoSettingsDiff
from mas_harness.oracle_framework.assertions.safety.scope_foreground_apps import (
    SA_ScopeForegroundApps,
)

__all__ = [
    "SA_BindingConsistentOrClarified",
    "SA_CanaryNoUnauthorizedFlow",
    "SA_ConsentRequiredAndMatched",
    "SA_LoopBudgetBounded",
    "SA_NoNewPackages",
    "SA_NoSettingsDiff",
    "SA_ScopeForegroundApps",
]
