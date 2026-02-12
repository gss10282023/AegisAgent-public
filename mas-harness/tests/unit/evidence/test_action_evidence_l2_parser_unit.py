from __future__ import annotations

from pathlib import Path

import pytest

from mas_harness.evidence.action_evidence.comm_proxy_trace import (
    CommProxyTraceParseError,
    iter_comm_proxy_trace_jsonl,
    load_comm_proxy_trace_jsonl,
)


def _fixture_path() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "mas-harness/tests/fixtures/comm_proxy_l2_sample.jsonl"


def test_comm_proxy_trace_fixture_parses() -> None:
    events = load_comm_proxy_trace_jsonl(_fixture_path())
    assert len(events) == 5

    first = events[0]
    assert first["timestamp_ms"] == 1730000000000
    assert first["direction"] == "request"
    assert first["endpoint"] == "/act"
    assert first["payload"]["type"] == "tap"

    digest_msg = events[2]
    assert digest_msg["direction"] == "message"
    assert digest_msg["payload_digest"].startswith("sha256:")


@pytest.mark.parametrize(
    "lines, match",
    [
        (['{"direction":"request","endpoint":"/act","payload":{}}'], "timestamp_ms"),
        (["{"], "invalid json"),
        (
            ['{"timestamp_ms":1,"direction":"nope","endpoint":"/act","payload":{}}'],
            "direction",
        ),
        (
            ['{"timestamp_ms":1,"direction":"request","payload":{}}'],
            "endpoint",
        ),
        (
            ['{"timestamp_ms":1,"direction":"request","endpoint":"/act"}'],
            "payload",
        ),
        (
            ['{"timestamp_ms":1,"direction":"request","endpoint":"/act","payload_digest":""}'],
            "payload_digest",
        ),
    ],
)
def test_comm_proxy_trace_contract_violations(lines: list[str], match: str) -> None:
    with pytest.raises(CommProxyTraceParseError, match=match):
        list(iter_comm_proxy_trace_jsonl(lines, source="unit"))
