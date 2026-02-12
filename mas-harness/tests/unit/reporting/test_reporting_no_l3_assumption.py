from __future__ import annotations

import pytest

from mas_harness.reporting import (
    bucket_action_evidence_by_action_level,
    bucket_hard_oracle_benign_regression_by_action_level,
)


def test_reporting_no_l3_assumption() -> None:
    # Phase3 reporting assumes the harness/registry never produces L3.
    # If it appears anywhere in summary.json inputs, fail fast instead of silently bucketing it.
    summaries = [
        {
            "agent_id": "agent_l3",
            "case_id": "conf_001_open_settings",
            "availability": "runnable",
            "oracle_source": "device_query",
            "oracle_decision": "inconclusive",
            "status": "inconclusive",
            "action_trace_level": "L3",
        }
    ]

    for fn in (
        bucket_hard_oracle_benign_regression_by_action_level,
        bucket_action_evidence_by_action_level,
    ):
        with pytest.raises(ValueError, match=r"L3"):
            fn(summaries)
