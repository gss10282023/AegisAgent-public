from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC
from typing import Any, List

from mas_harness.oracle_framework.types import (
    ALLOWED_ASSERTION_RESULTS_V0,
    ALLOWED_FACT_ORACLE_SOURCES_V0,
    ALLOWED_SEVERITIES_V0,
    FACT_SCHEMA_VERSION_V0,
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def fact_v0_errors(fact: MappingABC[str, Any]) -> List[str]:
    errors: List[str] = []

    if not _is_nonempty_str(fact.get("fact_id")):
        errors.append("fact_id must be a non-empty string")

    if fact.get("schema_version") != FACT_SCHEMA_VERSION_V0:
        errors.append(f"schema_version must be '{FACT_SCHEMA_VERSION_V0}'")

    digest = fact.get("digest")
    if not _is_nonempty_str(digest) or _SHA256_HEX_RE.match(str(digest)) is None:
        errors.append("digest must be a lowercase sha256 hex string")

    oracle_source = fact.get("oracle_source")
    if (
        not _is_nonempty_str(oracle_source)
        or str(oracle_source) not in ALLOWED_FACT_ORACLE_SOURCES_V0
    ):
        errors.append(f"oracle_source must be one of {sorted(ALLOWED_FACT_ORACLE_SOURCES_V0)}")

    refs = fact.get("evidence_refs")
    if not isinstance(refs, list) or not all(_is_nonempty_str(r) for r in refs):
        errors.append("evidence_refs must be list[str] (may be empty)")

    payload = fact.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload must be an object")

    return errors


def assert_fact_v0(fact: MappingABC[str, Any]) -> None:
    errors = fact_v0_errors(fact)
    if errors:
        raise ValueError("Fact v0 contract violation: " + "; ".join(errors))


def assertion_result_v0_errors(result: MappingABC[str, Any]) -> List[str]:
    errors: List[str] = []

    if not _is_nonempty_str(result.get("assertion_id")):
        errors.append("assertion_id must be a non-empty string")

    res = result.get("result")
    if not _is_nonempty_str(res) or str(res) not in ALLOWED_ASSERTION_RESULTS_V0:
        errors.append(f"result must be one of {sorted(ALLOWED_ASSERTION_RESULTS_V0)}")

    severity = result.get("severity")
    risk_bucket = result.get("risk_weight_bucket")
    if severity is None and risk_bucket is None:
        errors.append("either severity or risk_weight_bucket is required")
    if severity is not None:
        if (
            not _is_nonempty_str(severity)
            or str(severity).strip().lower() not in ALLOWED_SEVERITIES_V0
        ):
            errors.append(f"severity must be one of {sorted(ALLOWED_SEVERITIES_V0)}")
    if risk_bucket is not None and not _is_nonempty_str(risk_bucket):
        errors.append("risk_weight_bucket must be a non-empty string")

    for k in ("mapped_sp", "mapped_primitive", "mapped_boundary", "impact_level"):
        if k not in result:
            errors.append(f"missing field: {k}")
            continue
        v = result.get(k)
        if v is not None and not _is_nonempty_str(v):
            errors.append(f"{k} must be a non-empty string|null")

    refs = result.get("evidence_refs")
    if not isinstance(refs, list) or not all(_is_nonempty_str(r) for r in refs):
        errors.append("evidence_refs must be list[str] (may be empty)")

    applicable = result.get("applicable")
    if applicable is not None and not isinstance(applicable, bool):
        errors.append("applicable must be bool|null")

    inconclusive_reason = result.get("inconclusive_reason")
    if str(res) == "INCONCLUSIVE":
        if not _is_nonempty_str(inconclusive_reason):
            errors.append("inconclusive_reason is required for result=INCONCLUSIVE")
    else:
        if inconclusive_reason is not None and not isinstance(inconclusive_reason, str):
            errors.append("inconclusive_reason must be str|null")

    return errors


def assert_assertion_result_v0(result: MappingABC[str, Any]) -> None:
    errors = assertion_result_v0_errors(result)
    if errors:
        raise ValueError("AssertionResult v0 contract violation: " + "; ".join(errors))
