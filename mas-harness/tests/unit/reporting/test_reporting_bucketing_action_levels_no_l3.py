from __future__ import annotations

import json
from pathlib import Path

from mas_harness.reporting import (
    bucket_hard_oracle_benign_regression_by_action_level,
    write_hard_oracle_benign_regression_action_level_report,
)


def test_reporting_bucketing_action_levels_no_l3(tmp_path: Path) -> None:
    summaries: list[dict[str, object]] = [
        {
            "agent_id": "agent_l0",
            "case_id": "oracle_reg_example_0",
            "availability": "runnable",
            "oracle_source": "device_query",
            "oracle_decision": "pass",
            "status": "success",
            "action_trace_level": "L0",
        },
        {
            "agent_id": "agent_l1",
            "case_id": "oracle_reg_example_1",
            "availability": "runnable",
            "oracle_source": "device_query",
            "oracle_decision": "pass",
            "status": "success",
            "action_trace_level": "L1",
        },
        {
            "agent_id": "agent_l2",
            "case_id": "oracle_reg_example_2",
            "availability": "runnable",
            "oracle_source": "device_query",
            "oracle_decision": "pass",
            "status": "success",
            "action_trace_level": "L2",
        },
        # Not part of the hard-oracle benign regression subset; should be ignored by selection.
        {
            "agent_id": "agent_l0",
            "case_id": "conf_001_open_settings",
            "availability": "runnable",
            "oracle_source": "device_query",
            "oracle_decision": "inconclusive",
            "status": "inconclusive",
            "action_trace_level": "L0",
        },
    ]

    report = bucket_hard_oracle_benign_regression_by_action_level(summaries, min_unique_cases=3)
    assert set(report["buckets"].keys()) == {"L0", "L1", "L2"}
    assert "L3" not in report["buckets"]
    assert report["buckets"]["L0"]["unique_agents"] == ["agent_l0"]
    assert report["buckets"]["L1"]["unique_agents"] == ["agent_l1"]
    assert report["buckets"]["L2"]["unique_agents"] == ["agent_l2"]

    runs_dir = tmp_path / "runs"
    for i, summary in enumerate(summaries):
        episode_dir = runs_dir / f"run_{i:04d}" / "episode_0000"
        episode_dir.mkdir(parents=True, exist_ok=True)
        (episode_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    out_path = tmp_path / "report.json"
    payload = write_hard_oracle_benign_regression_action_level_report(
        runs_dir=runs_dir,
        out_path=out_path,
        min_unique_cases=3,
    )
    assert out_path.exists()
    assert set(payload["buckets"].keys()) == {"L0", "L1", "L2"}
