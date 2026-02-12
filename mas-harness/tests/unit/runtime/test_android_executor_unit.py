from __future__ import annotations

import shlex

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


def test_android_executor_tap_physical_px() -> None:
    ctr = _FakeController()
    ex = AndroidExecutor(controller=ctr, timeout_s=0.1)
    res = ex.execute(
        {"type": "tap", "coord_space": "physical_px", "coord": {"x_px": 12, "y_px": 34}}
    )
    assert res["ok"] is True
    assert [shlex.split(cmd) for cmd in ctr.shell_cmds] == [["input", "tap", "12", "34"]]


def test_android_executor_type_multiline_text() -> None:
    ctr = _FakeController()
    ex = AndroidExecutor(controller=ctr, timeout_s=0.1)
    res = ex.execute({"type": "type", "text": "hello world\nnext"})
    assert res["ok"] is True
    assert [shlex.split(cmd) for cmd in ctr.shell_cmds] == [
        ["input", "text", "hello%sworld"],
        ["input", "keyevent", "66"],
        ["input", "text", "next"],
    ]


def test_android_executor_open_url() -> None:
    ctr = _FakeController()
    ex = AndroidExecutor(controller=ctr, timeout_s=0.1)
    res = ex.execute({"type": "open_url", "url": "https://example.com"})
    assert res["ok"] is True
    assert [shlex.split(cmd) for cmd in ctr.shell_cmds] == [
        ["am", "start", "-a", "android.intent.action.VIEW", "-d", "https://example.com"]
    ]


def test_android_executor_open_app_settings_alias() -> None:
    ctr = _FakeController(foreground_package="com.android.settings")
    ex = AndroidExecutor(controller=ctr, timeout_s=0.1, open_app_timeout_s=0.1)
    res = ex.execute({"type": "open_app", "package": "settings"})
    assert res["ok"] is True
    assert shlex.split(ctr.shell_cmds[0]) == ["am", "start", "-a", "android.settings.SETTINGS"]


def test_android_executor_press_back_and_home() -> None:
    ctr = _FakeController()
    ex = AndroidExecutor(controller=ctr, timeout_s=0.1)
    res = ex.execute({"type": "press_back"})
    assert res["ok"] is True
    res = ex.execute({"type": "home"})
    assert res["ok"] is True
    assert [shlex.split(cmd) for cmd in ctr.shell_cmds] == [
        ["input", "keyevent", "KEYCODE_BACK"],
        ["input", "keyevent", "KEYCODE_HOME"],
    ]
