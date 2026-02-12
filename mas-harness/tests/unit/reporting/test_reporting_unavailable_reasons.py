from __future__ import annotations

import json
from pathlib import Path

import yaml

from mas_harness.reporting import (
    bucket_unavailable_reasons,
    write_unavailable_reasons_distribution_report,
)


def test_reporting_bucket_unavailable_reasons() -> None:
    registry_entries = [
        {"agent_id": "a", "availability": "unavailable", "unavailable_reason": "not_integrated"},
        {"agent_id": "b", "availability": "unavailable", "unavailable_reason": "policy_blocked"},
        {"agent_id": "c", "availability": "unavailable", "unavailable_reason": "not_integrated"},
        {"agent_id": "d", "availability": "runnable"},
        {"agent_id": "e", "availability": "audit_only"},
        {"agent_id": "f", "availability": "unavailable"},  # should bucket as missing reason
    ]

    report = bucket_unavailable_reasons(registry_entries)
    assert report["total_registry_entries"] == len(registry_entries)
    assert report["total_unavailable_entries"] == 4

    buckets = report["buckets"]
    assert set(buckets.keys()) == {"missing_unavailable_reason", "not_integrated", "policy_blocked"}
    assert buckets["not_integrated"]["total_agents"] == 2
    assert buckets["not_integrated"]["agent_ids"] == ["a", "c"]
    assert buckets["policy_blocked"]["agent_ids"] == ["b"]
    assert buckets["missing_unavailable_reason"]["agent_ids"] == ["f"]


def test_reporting_write_unavailable_reasons_distribution_report(tmp_path: Path) -> None:
    registry_path = tmp_path / "agent_registry.yaml"
    registry_entries = [
        {"agent_id": "a", "availability": "unavailable", "unavailable_reason": "not_integrated"},
        {"agent_id": "b", "availability": "runnable"},
        {"agent_id": "c", "availability": "unavailable", "unavailable_reason": "not_integrated"},
    ]
    registry_path.write_text(
        yaml.safe_dump(registry_entries, sort_keys=False, allow_unicode=True).rstrip() + "\n",
        encoding="utf-8",
    )

    out_path = tmp_path / "report.json"
    payload = write_unavailable_reasons_distribution_report(
        registry_path=registry_path,
        out_path=out_path,
    )
    assert out_path.exists()

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data == payload
    assert data["registry_path"] == str(registry_path)
    dist = data["unavailable_reasons_distribution"]
    assert dist["total_unavailable_entries"] == 2
    assert dist["buckets"]["not_integrated"]["agent_ids"] == ["a", "c"]
