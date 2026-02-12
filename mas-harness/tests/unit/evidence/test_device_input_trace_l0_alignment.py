from __future__ import annotations

import json
from pathlib import Path

from mas_harness.evidence import EvidenceWriter
from mas_harness.runtime.android.controller import AdbResult
from mas_harness.runtime.android.executor import AndroidExecutor


class _FakeController:
    def __init__(self, *, foreground_package: str | None = None) -> None:
        self.shell_cmds: list[str] = []
        self._foreground_package = foreground_package

    def adb_shell(self, command: str, **kwargs) -> AdbResult:  # noqa: ARG002
        self.shell_cmds.append(command)
        return AdbResult(args=["adb", "shell", command], stdout="", stderr="", returncode=0)

    def get_foreground(self, **kwargs):  # noqa: ARG002
        return {"package": self._foreground_package, "activity": None}


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        assert isinstance(obj, dict)
        out.append(obj)
    return out


def test_device_input_trace_l0_alignment_agent_action_trace_matches_ref_step_idx(
    tmp_path: Path,
) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case", seed=0, run_mode="public")
    try:
        ctr = _FakeController(foreground_package="com.android.settings")
        ex = AndroidExecutor(
            controller=ctr,
            timeout_s=0.1,
            open_app_timeout_s=0.1,
            device_input_writer=writer,
            device_input_source_level="L0",
        )

        raw_actions = [
            {"type": "tap", "coord_space": "physical_px", "coord": {"x_px": 12, "y_px": 34}},
            {
                "type": "swipe",
                "coord_space": "physical_px",
                "start": {"x_px": 1, "y_px": 2},
                "end": {"x_px": 3, "y_px": 4},
                "duration_ms": 123,
            },
            {"type": "type", "text": "hi"},
            {"type": "press_back"},
            {"type": "home"},
            {"type": "open_url", "url": "https://example.com"},
            {"type": "open_app", "package": "settings"},
            {"type": "wait", "duration_ms": 0},
        ]

        for step_idx, raw_action in enumerate(raw_actions):
            normalized_action = writer.record_agent_action(step_idx, raw_action)
            res = ex.execute(normalized_action)
            assert isinstance(res, dict)

        agent_action_events = [
            e
            for e in _read_jsonl(writer.paths.agent_action_trace)
            if e.get("event") == "agent_action"
        ]
        device_input_events = _read_jsonl(writer.paths.device_input_trace)

        assert len(agent_action_events) == len(device_input_events)

        # Join by `ref_step_idx` per spec:
        # agent_action_trace.step_idx == device_input_trace.ref_step_idx.
        assert [e["step_idx"] for e in agent_action_events] == [
            e["ref_step_idx"] for e in device_input_events
        ]

        # L0 strict: device_input_trace.step_idx == device_input_trace.ref_step_idx.
        assert [e["step_idx"] for e in device_input_events] == [
            e["ref_step_idx"] for e in device_input_events
        ]
    finally:
        writer.close()
