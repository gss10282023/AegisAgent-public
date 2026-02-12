from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from mas_harness.evidence.action_evidence.comm_proxy_trace import load_comm_proxy_trace_jsonl
from mas_harness.evidence.action_evidence.l2_http_recorder import HttpJsonActionRecorder


def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(req, timeout=2.0) as resp:
        body = resp.read()
        return int(resp.getcode()), json.loads(body.decode("utf-8"))


def _get_json(url: str) -> tuple[int, Any]:
    with urlopen(url, timeout=2.0) as resp:
        body = resp.read()
        return int(resp.getcode()), json.loads(body.decode("utf-8"))


def test_http_json_action_recorder_writes_comm_proxy_trace_jsonl(tmp_path: Path) -> None:
    trace_path = tmp_path / "comm_proxy_trace.jsonl"
    with HttpJsonActionRecorder(trace_path) as recorder:
        status, resp = _post_json(
            recorder.base_url + "/act",
            {"type": "tap", "x": 120, "y": 340, "coord_space": "physical_px", "extra": {"k": "v"}},
        )
        assert status == 200
        assert resp["ok"] is True
        assert isinstance(resp.get("action_id"), str)

        status, resp = _get_json(recorder.base_url + "/health")
        assert status == 200
        assert resp["status"] == "ok"

    events = load_comm_proxy_trace_jsonl(trace_path)
    assert len(events) == 4

    act_req, act_resp, health_req, health_resp = events
    assert act_req["direction"] == "request"
    assert act_req["endpoint"] == "/act"
    assert act_req["payload"]["type"] == "tap"

    assert act_resp["direction"] == "response"
    assert act_resp["endpoint"] == "/act"
    assert act_resp["status"] == 200
    assert act_resp["payload"]["ok"] is True

    assert health_req["direction"] == "request"
    assert health_req["endpoint"] == "/health"
    assert health_req["payload"] == {}

    assert health_resp["direction"] == "response"
    assert health_resp["endpoint"] == "/health"
    assert health_resp["status"] == 200
    assert health_resp["payload"]["status"] == "ok"


def test_http_json_action_recorder_rejects_invalid_action(tmp_path: Path) -> None:
    trace_path = tmp_path / "comm_proxy_trace.jsonl"
    with HttpJsonActionRecorder(trace_path) as recorder:
        with pytest.raises(HTTPError) as exc:
            _post_json(recorder.base_url + "/act", {"type": ""})
        assert exc.value.code == 400

    events = load_comm_proxy_trace_jsonl(trace_path)
    assert len(events) == 2
    assert events[0]["direction"] == "request"
    assert events[0]["endpoint"] == "/act"
    assert events[1]["direction"] == "response"
    assert events[1]["endpoint"] == "/act"
    assert events[1]["status"] == 400
