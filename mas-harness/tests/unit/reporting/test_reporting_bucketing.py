from __future__ import annotations

import pytest

from mas_harness.reporting import bucket_action_evidence_by_action_level


def test_reporting_bucketing_action_evidence_levels() -> None:
    summaries = [
        {"agent_id": "agent_l0", "case_id": "c0", "action_trace_level": "L0"},
        {"agent_id": "agent_l1", "case_id": "c1", "action_trace_level": "l1"},
        {"agent_id": "agent_l2", "case_id": "c2", "action_trace_level": "L2"},
        {"agent_id": "agent_none", "case_id": "c3", "action_trace_level": "none"},
        {"agent_id": "agent_missing", "case_id": "c4"},
        {"agent_id": "agent_blank", "case_id": "c5", "action_trace_level": "   "},
        {"agent_id": "agent_non_string", "case_id": "c6", "action_trace_level": 123},
    ]

    report = bucket_action_evidence_by_action_level(summaries)
    assert report["total_episodes"] == len(summaries)
    assert set(report["buckets"].keys()) == {"L0", "L1", "L2", "none"}
    assert "L3" not in report["buckets"]

    assert report["buckets"]["L0"]["unique_agents"] == ["agent_l0"]
    assert report["buckets"]["L1"]["unique_agents"] == ["agent_l1"]
    assert report["buckets"]["L2"]["unique_agents"] == ["agent_l2"]

    # Missing/blank/non-string action_trace_level values are treated as "none".
    assert report["buckets"]["none"]["total_episodes"] == 4
    assert set(report["buckets"]["none"]["unique_agents"]) == {
        "agent_blank",
        "agent_missing",
        "agent_non_string",
        "agent_none",
    }


def test_reporting_bucketing_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match=r"unexpected action_trace_level"):
        bucket_action_evidence_by_action_level(
            [{"agent_id": "bad", "case_id": "c0", "action_trace_level": "L9"}]
        )
