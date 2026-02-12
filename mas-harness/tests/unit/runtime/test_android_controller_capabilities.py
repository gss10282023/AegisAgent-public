from __future__ import annotations

import shlex
import subprocess
from types import SimpleNamespace

from mas_harness.oracles.zoo.adb_shell import AdbShellExpectRegexOracle
from mas_harness.oracles.zoo.base import OracleContext
from mas_harness.runtime.android.controller import AdbResult, AndroidController


def test_android_controller_adb_shell_accepts_timeout_ms(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    ctr = AndroidController(adb_path="adb", serial="emulator-5554", timeout_s=123.0)
    ctr.adb_shell("echo ok", timeout_ms=1500, check=False)

    assert calls, "expected subprocess.run to be called"
    assert calls[0]["kwargs"]["timeout"] == 1.5


def test_android_controller_content_query_builds_shell_safe_command(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_adb_shell(cmd: str, **kwargs) -> AdbResult:
        captured["cmd"] = cmd
        return AdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

    ctr = AndroidController(adb_path="adb", serial="s")
    monkeypatch.setattr(ctr, "adb_shell", fake_adb_shell)

    ctr.content_query(
        uri="content://sms/sent",
        projection=["_id", "address", "body"],
        where="address='123' AND body LIKE '%hello world%'",
        sort="date DESC",
        limit=1,
        check=False,
    )

    tokens = shlex.split(captured["cmd"])
    assert tokens == [
        "content",
        "query",
        "--uri",
        "content://sms/sent",
        "--projection",
        "_id,address,body",
        "--where",
        "address='123' AND body LIKE '%hello world%'",
        "--sort",
        "date DESC",
        "--limit",
        "1",
    ]


def test_android_controller_get_foreground_parses_resumed_activity(monkeypatch) -> None:
    def fake_adb_shell(cmd: str, **kwargs) -> AdbResult:
        if cmd == "dumpsys activity activities":
            return AdbResult(
                args=["adb", "shell", cmd],
                stdout=("mResumedActivity: ActivityRecord{... u0 com.example/.MainActivity t12}\n"),
                stderr="",
                returncode=0,
            )
        return AdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=1)

    ctr = AndroidController(adb_path="adb", serial="s")
    monkeypatch.setattr(ctr, "adb_shell", fake_adb_shell)

    fg = ctr.get_foreground()
    assert fg["package"] == "com.example"
    assert fg["activity"] == "com.example.MainActivity"


def test_android_controller_get_foreground_parses_resumed_activity_modern(monkeypatch) -> None:
    def fake_adb_shell(cmd: str, **kwargs) -> AdbResult:
        if cmd == "dumpsys activity activities":
            return AdbResult(
                args=["adb", "shell", cmd],
                stdout=("ResumedActivity: ActivityRecord{1 u0 com.modern/.HomeActivity t2}\n"),
                stderr="",
                returncode=0,
            )
        return AdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=1)

    ctr = AndroidController(adb_path="adb", serial="s")
    monkeypatch.setattr(ctr, "adb_shell", fake_adb_shell)

    fg = ctr.get_foreground()
    assert fg["package"] == "com.modern"
    assert fg["activity"] == "com.modern.HomeActivity"


def test_android_controller_get_screen_info_parses_physical_frame_boundary_px(monkeypatch) -> None:
    def fake_adb_shell(cmd: str, **kwargs) -> AdbResult:
        if cmd == "wm size":
            return AdbResult(
                args=["adb", "shell", cmd],
                stdout="Physical size: 1080x2424\n",
                stderr="",
                returncode=0,
            )
        if cmd == "wm density":
            return AdbResult(
                args=["adb", "shell", cmd],
                stdout="Physical density: 420\n",
                stderr="",
                returncode=0,
            )
        if cmd == "dumpsys window displays":
            return AdbResult(
                args=["adb", "shell", cmd],
                stdout=(
                    "mDisplayRotation=ROTATION_0\n"
                    "mDecorInsetsInfo:\n"
                    "  ROTATION_0={overrideNonDecorFrame=[0,142][1080,2361]}\n"
                ),
                stderr="",
                returncode=0,
            )
        return AdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=1)

    ctr = AndroidController(adb_path="adb", serial="s")
    monkeypatch.setattr(ctr, "adb_shell", fake_adb_shell)
    info = ctr.get_screen_info()
    assert info["surface_orientation"] == 0
    assert info["physical_frame_boundary_px"] == {
        "left": 0,
        "top": 142,
        "right": 1080,
        "bottom": 2361,
    }


def test_android_controller_get_screen_info_parses_physical_frame_boundary_px_from_insets(
    monkeypatch,
) -> None:
    def fake_adb_shell(cmd: str, **kwargs) -> AdbResult:
        if cmd == "wm size":
            return AdbResult(
                args=["adb", "shell", cmd],
                stdout="Physical size: 1080x2424\n",
                stderr="",
                returncode=0,
            )
        if cmd == "wm density":
            return AdbResult(
                args=["adb", "shell", cmd],
                stdout="Physical density: 420\n",
                stderr="",
                returncode=0,
            )
        if cmd == "dumpsys window displays":
            return AdbResult(
                args=["adb", "shell", cmd],
                stdout=(
                    "SurfaceOrientation: 0\n"
                    "mDecorInsetsInfo:\n"
                    "  ROTATION_0={overrideNonDecorInsets=[0,10][0,20]}\n"
                ),
                stderr="",
                returncode=0,
            )
        return AdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=1)

    ctr = AndroidController(adb_path="adb", serial="s")
    monkeypatch.setattr(ctr, "adb_shell", fake_adb_shell)
    info = ctr.get_screen_info()
    assert info["physical_frame_boundary_px"] == {
        "left": 0,
        "top": 10,
        "right": 1080,
        "bottom": 2404,
    }


def test_android_controller_probe_env_capabilities_is_best_effort(monkeypatch) -> None:
    ctr = AndroidController(adb_path="adb", serial="s")

    def boom_version() -> str:
        raise RuntimeError("no adb")

    def boom_shell(*args, **kwargs):
        raise FileNotFoundError("adb not found")

    monkeypatch.setattr(ctr, "adb_version", boom_version)
    monkeypatch.setattr(ctr, "adb_shell", boom_shell)

    caps = ctr.probe_env_capabilities()
    assert caps["available"] is False
    assert "android_api_level" in caps
    assert "can_pull_data" in caps
    assert isinstance(caps["notes"], list)


def test_oracle_gates_missing_controller_capability() -> None:
    oracle = AdbShellExpectRegexOracle(shell_cmd="echo ok", expect_regex=r"ok")
    ctx = OracleContext.from_task_and_controller(
        task_spec={"task_id": "t", "user_goal": "x"},
        controller=object(),
    )
    evidence = oracle.post_check(ctx)
    assert evidence
    assert evidence[0]["decision"]["conclusive"] is False
    assert evidence[0].get("missing_capabilities") == ["adb_shell"]


def test_android_smoke_detects_single_device_serial(monkeypatch) -> None:
    from mas_harness.tools.android_smoke import detect_single_device_serial

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["adb", "devices"]
        return SimpleNamespace(
            stdout="List of devices attached\nemulator-5554\tdevice\n\n",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert detect_single_device_serial(adb_path="adb") == "emulator-5554"
