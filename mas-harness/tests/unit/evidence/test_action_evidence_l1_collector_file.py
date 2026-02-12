from __future__ import annotations

from pathlib import Path

import pytest

from mas_harness.evidence.action_evidence.base import ActionEvidenceCollectionError, EventStreamSpec
from mas_harness.evidence.action_evidence.l1_agent_events import L1AgentEventsCollector


def _fixture_path() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "mas-harness" / "tests" / "fixtures" / "agent_events_l1_sample.jsonl"


def test_l1_collector_reads_path_on_host_absolute() -> None:
    collector = L1AgentEventsCollector(
        event_stream=EventStreamSpec(
            format="agent_events_v1",
            path_on_host=str(_fixture_path()),
        )
    )
    events = collector.collect()
    assert len(events) == 8
    assert events[0]["type"] == "tap"


def test_l1_collector_resolves_repo_root_relative_path(tmp_path: Path) -> None:
    rel = Path("runs/unit/agent_events.jsonl")
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_fixture_path().read_text(encoding="utf-8"), encoding="utf-8")

    collector = L1AgentEventsCollector(
        event_stream=EventStreamSpec(
            format="agent_events_v1",
            path_on_host=str(rel),
        ),
        repo_root=tmp_path,
    )
    events = collector.collect()
    assert len(events) == 8


def test_l1_collector_missing_file_raises(tmp_path: Path) -> None:
    collector = L1AgentEventsCollector(
        event_stream=EventStreamSpec(
            format="agent_events_v1",
            path_on_host="runs/missing/agent_events.jsonl",
        ),
        repo_root=tmp_path,
    )
    with pytest.raises(ActionEvidenceCollectionError, match="not found"):
        collector.collect()


def test_l1_collector_rejects_unsupported_format() -> None:
    collector = L1AgentEventsCollector(
        event_stream=EventStreamSpec(
            format="unsupported_v0",
            path_on_host=str(_fixture_path()),
        )
    )
    with pytest.raises(ActionEvidenceCollectionError, match="unsupported"):
        collector.collect()
