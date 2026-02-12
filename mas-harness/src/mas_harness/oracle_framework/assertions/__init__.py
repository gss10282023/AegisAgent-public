"""Oracle Framework v1: Assertions.

Assertions evaluate extracted Facts + case/policy context into PASS/FAIL/INCONCLUSIVE
results suitable for audit-first evaluation.
"""

from __future__ import annotations

from mas_harness.oracle_framework.assertions.base import Assertion
from mas_harness.oracle_framework.assertions.safety import (
    SA_BindingConsistentOrClarified,
    SA_CanaryNoUnauthorizedFlow,
    SA_ConsentRequiredAndMatched,
    SA_LoopBudgetBounded,
    SA_NoNewPackages,
    SA_NoSettingsDiff,
    SA_ScopeForegroundApps,
)
from mas_harness.oracle_framework.assertions.success import SuccessOracleAssertion

__all__ = [
    "Assertion",
    "SA_BindingConsistentOrClarified",
    "SA_CanaryNoUnauthorizedFlow",
    "SA_ConsentRequiredAndMatched",
    "SA_LoopBudgetBounded",
    "SA_NoNewPackages",
    "SA_NoSettingsDiff",
    "SA_ScopeForegroundApps",
    "SuccessOracleAssertion",
]
