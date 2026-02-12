"""Reporting helpers.

Most of the reporting semantics in this repo are Phase3-specific; they live in
`mas_harness.reporting.phase3_bucketing` and are re-exported here for backward
compatibility.
"""

from __future__ import annotations

from mas_harness.reporting.aggregate import (
    aggregate_summaries,
    build_aggregate_report,
    write_aggregate_report,
)
from mas_harness.reporting.phase3_bucketing import (
    PHASE3_ACTION_EVIDENCE_BUCKETS,
    PHASE3_ACTION_TRACE_LEVEL_BUCKETS,
    assert_no_l3_action_trace_level,
    bucket_action_evidence_by_action_level,
    bucket_hard_oracle_benign_regression_by_action_level,
    bucket_unavailable_reasons,
    find_summary_paths,
    is_hard_oracle_benign_regression_summary,
    load_json_object,
    normalize_action_trace_level,
    normalize_availability,
    normalize_unavailable_reason,
    write_action_evidence_distribution_report,
    write_hard_oracle_benign_regression_action_level_report,
    write_unavailable_reasons_distribution_report,
)

__all__ = [
    "PHASE3_ACTION_TRACE_LEVEL_BUCKETS",
    "PHASE3_ACTION_EVIDENCE_BUCKETS",
    "aggregate_summaries",
    "build_aggregate_report",
    "write_aggregate_report",
    "find_summary_paths",
    "load_json_object",
    "normalize_action_trace_level",
    "normalize_availability",
    "normalize_unavailable_reason",
    "assert_no_l3_action_trace_level",
    "is_hard_oracle_benign_regression_summary",
    "bucket_hard_oracle_benign_regression_by_action_level",
    "bucket_action_evidence_by_action_level",
    "bucket_unavailable_reasons",
    "write_action_evidence_distribution_report",
    "write_hard_oracle_benign_regression_action_level_report",
    "write_unavailable_reasons_distribution_report",
]
