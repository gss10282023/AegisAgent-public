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


def test_device_input_trace_l0_executor_writes_one_line_per_action(tmp_path: Path) -> None:
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

        actions = [
            {
                "step_idx": 0,
                "type": "tap",
                "coord_space": "physical_px",
                "coord": {"x_px": 12, "y_px": 34},
            },
            {
                "step_idx": 1,
                "type": "swipe",
                "coord_space": "physical_px",
                "start": {"x_px": 1, "y_px": 2},
                "end": {"x_px": 3, "y_px": 4},
                "duration_ms": 123,
            },
            {"step_idx": 2, "type": "type", "text": "hi"},
            {"step_idx": 3, "type": "press_back"},
            {"step_idx": 4, "type": "home"},
            {"step_idx": 5, "type": "open_url", "url": "https://example.com"},
            {"step_idx": 6, "type": "open_app", "package": "settings"},
            {"step_idx": 7, "type": "wait", "duration_ms": 0},
        ]

        for a in actions:
            res = ex.execute(a)
            assert isinstance(res, dict)

        events = _read_jsonl(writer.paths.device_input_trace)
        assert len(events) == len(actions)
        assert [e["source_level"] for e in events] == ["L0"] * len(actions)
        assert [e["step_idx"] for e in events] == list(range(len(actions)))
        assert [e["ref_step_idx"] for e in events] == list(range(len(actions)))
        assert all(isinstance(e.get("timestamp_ms"), int) and e["timestamp_ms"] > 0 for e in events)

        tap = events[0]
        assert tap["event_type"] == "tap"
        assert tap["payload"]["coord_space"] == "physical_px"
        assert tap["payload"]["x"] == 12
        assert tap["payload"]["y"] == 34

        swipe = events[1]
        assert swipe["event_type"] == "swipe"
        assert swipe["payload"]["coord_space"] == "physical_px"
        assert swipe["payload"]["start"] == {"x": 1, "y": 2}
        assert swipe["payload"]["end"] == {"x": 3, "y": 4}
    finally:
        writer.close()
