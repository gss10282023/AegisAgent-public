from __future__ import annotations

from pathlib import Path

import pytest

from mas_harness.evidence.action_evidence.agent_events_v1 import (
    AgentEventsV1ParseError,
    iter_agent_events_v1_jsonl,
    load_agent_events_v1_jsonl,
)


def _fixture_path() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "mas-harness/tests/fixtures/agent_events_l1_sample.jsonl"


def test_agent_events_v1_fixture_parses() -> None:
    events = load_agent_events_v1_jsonl(_fixture_path())
    assert len(events) == 8

    first = events[0]
    assert first["type"] == "tap"
    assert first["timestamp_ms"] == 1730000000000
    assert first["coord_space"] == "physical_px"
    assert first["x"] == 120
    assert first["y"] == 340

    swipe = events[1]
    assert swipe["type"] == "swipe"
    assert swipe["coord_space"] == "physical_px"
    assert swipe["start"]["x"] == 100
    assert swipe["start"]["y"] == 200
    assert swipe["end"]["x"] == 300
    assert swipe["end"]["y"] == 400


@pytest.mark.parametrize(
    "lines, match",
    [
        (['{"type":"tap"}'], "timestamp_ms"),
        (["{"], "invalid json"),
        (['{"timestamp_ms":1,"type":"tap","x":1,"y":2}'], "coord_space"),
        (
            ['{"timestamp_ms":1,"type":"tap","coord_space":"physical_px","x":1}'],
            "both 'x' and 'y'",
        ),
        (
            [
                '{"timestamp_ms":1,"type":"swipe","coord_space":"physical_px","start":[],"end":{"x":1,"y":2}}'
            ],
            "'start' and 'end' must be objects",
        ),
        (
            [
                '{"timestamp_ms":1,"type":"swipe","coord_space":"physical_px","x":1,"y":2,"start":{"x":1,"y":2},"end":{"x":3,"y":4}}'
            ],
            "both x/y and start/end",
        ),
    ],
)
def test_agent_events_v1_contract_violations(lines: list[str], match: str) -> None:
    with pytest.raises(AgentEventsV1ParseError, match=match):
        list(iter_agent_events_v1_jsonl(lines, source="unit"))
