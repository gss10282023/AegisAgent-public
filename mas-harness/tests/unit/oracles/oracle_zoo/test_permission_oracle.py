from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.settings.permissions import PermissionOracle
from mas_harness.oracles.zoo.utils.time_window import EpisodeTime


@dataclass(frozen=True)
class FakeAdbResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int


class FakeController:
    def __init__(
        self,
        *,
        dumpsys_outputs: list[str],
        now_device_ms: int,
        serial: str = "FAKE_SERIAL",
    ) -> None:
        self.serial = serial
        self._dumpsys_outputs = list(dumpsys_outputs)
        self._dumpsys_calls = 0
        self._now_device_ms = int(now_device_ms)

    def adb_shell(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        timeout_ms: int | None = None,
        check: bool = True,
    ) -> FakeAdbResult:
        _ = timeout_s, timeout_ms, check
        cmd = str(command)

        if cmd.startswith("date +%s%3N"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(self._now_device_ms),
                stderr="",
                returncode=0,
            )
        if cmd.startswith("date +%s"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(self._now_device_ms // 1000),
                stderr="",
                returncode=0,
            )

        if cmd.startswith("dumpsys package "):
            idx = min(self._dumpsys_calls, len(self._dumpsys_outputs) - 1)
            stdout = self._dumpsys_outputs[idx] if self._dumpsys_outputs else ""
            self._dumpsys_calls += 1
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=stdout,
                stderr="",
                returncode=0,
            )

        return FakeAdbResult(
            args=["adb", "shell", cmd],
            stdout="",
            stderr="",
            returncode=0,
        )


def _fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ctx(*, controller: Any, t0_device_ms: int, episode_dir: Path) -> OracleContext:
    episode_time = EpisodeTime(t0_host_utc_ms=0, t0_device_epoch_ms=t0_device_ms, slack_ms=0)
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=episode_time, episode_dir=episode_dir
    )


def test_permission_granted_match(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    fixture_path = tests_dir / "fixtures" / "dumpsys_package_com_example_app_permissions_modern.txt"
    stdout_pre = _fixture(fixture_path)
    stdout_post = stdout_pre.replace(
        "android.permission.ACCESS_FINE_LOCATION: granted=false, flags=[ USER_SET ]",
        "android.permission.ACCESS_FINE_LOCATION: granted=true, flags=[ USER_SET ]",
    )

    t0 = 1_700_000_000_000
    now = t0 + 10_000
    controller = FakeController(dumpsys_outputs=[stdout_pre, stdout_post], now_device_ms=now)
    from mas_harness.oracles.zoo.settings.permissions import PermissionCheck

    oracle = PermissionOracle(
        package="com.example.app",
        checks=[
            PermissionCheck(
                permission="android.permission.ACCESS_FINE_LOCATION",
                expected_granted=True,
                require_change_in_window=True,
            )
        ],
        user_id=0,
    )

    ctx = _ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)
    pre_ev = oracle.pre_check(ctx)
    post_ev = oracle.post_check(ctx)

    for ev in pre_ev + post_ev:
        assert_oracle_event_v0(ev)

    decision = decision_from_evidence(post_ev, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True

    preview = post_ev[0].get("result_preview") or {}
    perms = preview.get("permissions") or {}
    assert perms.get("android.permission.ACCESS_FINE_LOCATION") is True


def test_permission_revoked_match(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    fixture_path = tests_dir / "fixtures" / "dumpsys_package_com_example_app_permissions_legacy.txt"
    stdout_pre = _fixture(fixture_path)
    stdout_post = stdout_pre.replace("\n      android.permission.CAMERA\n", "\n")

    t0 = 1_700_000_000_000
    now = t0 + 10_000
    controller = FakeController(dumpsys_outputs=[stdout_pre, stdout_post], now_device_ms=now)
    from mas_harness.oracles.zoo.settings.permissions import PermissionCheck

    oracle = PermissionOracle(
        package="com.example.app",
        checks=[
            PermissionCheck(
                permission="android.permission.CAMERA",
                expected_granted=False,
                require_change_in_window=True,
            )
        ],
        user_id=0,
    )

    ctx = _ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)
    _ = oracle.pre_check(ctx)
    post_ev = oracle.post_check(ctx)

    decision = decision_from_evidence(post_ev, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True


@pytest.mark.parametrize(
    "package,permission,expected_granted,expect_conclusive",
    [
        ("com.wrong.app", "android.permission.CAMERA", True, False),
        ("com.example.app", "android.permission.NOT_A_REAL_PERMISSION", True, True),
    ],
)
def test_negative_wrong_pkg_or_permission(
    tmp_path: Path,
    package: str,
    permission: str,
    expected_granted: bool,
    expect_conclusive: bool,
) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    fixture_path = tests_dir / "fixtures" / "dumpsys_package_com_example_app_permissions_modern.txt"
    stdout = _fixture(fixture_path)

    t0 = 1_700_000_000_000
    now = t0 + 10_000
    controller = FakeController(dumpsys_outputs=[stdout, stdout], now_device_ms=now)
    from mas_harness.oracles.zoo.settings.permissions import PermissionCheck

    oracle = PermissionOracle(
        package=package,
        checks=[
            PermissionCheck(
                permission=permission,
                expected_granted=expected_granted,
                require_change_in_window=True,
            )
        ],
        user_id=0,
    )

    ctx = _ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)
    _ = oracle.pre_check(ctx)
    post_ev = oracle.post_check(ctx)

    decision = decision_from_evidence(post_ev, oracle_id=oracle.oracle_id)
    assert decision["success"] is False
    assert decision["conclusive"] is expect_conclusive
