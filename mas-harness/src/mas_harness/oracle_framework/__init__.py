"""Oracle Framework v1 (Phase 4).

This package defines the shared vocabulary for audit-first evaluation:
- Detectors: extract stable, traceable facts from an EvidencePack.
- Assertions: evaluate facts (+ policy/case context) into PASS/FAIL/INCONCLUSIVE.
"""

from __future__ import annotations

from mas_harness.oracle_framework.assertion_base import Assertion
from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.schema_validators import (
    assert_assertion_result_v0,
    assert_fact_v0,
)
from mas_harness.oracle_framework.store import FactStore, MissingFactError
from mas_harness.oracle_framework.types import (
    ALLOWED_ASSERTION_RESULTS_V0,
    ALLOWED_FACT_ORACLE_SOURCES_V0,
    ALLOWED_SEVERITIES_V0,
    FACT_SCHEMA_VERSION_V0,
    AssertionResult,
    Fact,
)

__all__ = [
    "ALLOWED_ASSERTION_RESULTS_V0",
    "ALLOWED_FACT_ORACLE_SOURCES_V0",
    "ALLOWED_SEVERITIES_V0",
    "FACT_SCHEMA_VERSION_V0",
    "Assertion",
    "AssertionResult",
    "Detector",
    "Fact",
    "FactStore",
    "MissingFactError",
    "assert_assertion_result_v0",
    "assert_fact_v0",
]
