from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.settings.notification_permission import NotificationPermissionOracle


@dataclass(frozen=True)
class FakeAdbResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int


class FakeController:
    def __init__(self, *, dumpsys_outputs: Sequence[str], serial: str = "FAKE_SERIAL") -> None:
        self.serial = serial
        self._outputs = list(dumpsys_outputs)
        self._calls = 0

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

        if cmd.startswith("dumpsys package "):
            idx = min(self._calls, len(self._outputs) - 1)
            stdout = self._outputs[idx] if self._outputs else ""
            self._calls += 1
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


def _ctx(*, controller: Any, episode_dir: Path) -> OracleContext:
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=None, episode_dir=episode_dir
    )


def test_post_notifications_granted_match(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    denied = _fixture(
        tests_dir / "fixtures" / "dumpsys_package_com_example_app_post_notifications_denied.txt"
    )
    granted = _fixture(
        tests_dir / "fixtures" / "dumpsys_package_com_example_app_post_notifications_granted.txt"
    )

    controller = FakeController(dumpsys_outputs=[denied, granted])
    oracle = NotificationPermissionOracle(
        package="com.example.app",
        expected_granted=True,
        require_change_in_window=True,
    )
    ctx = _ctx(controller=controller, episode_dir=tmp_path)

    pre_ev = oracle.pre_check(ctx)
    post_ev = oracle.post_check(ctx)

    for ev in pre_ev + post_ev:
        assert_oracle_event_v0(ev)

    decision = decision_from_evidence(post_ev, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True


def test_parse_failure_inconclusive(tmp_path: Path) -> None:
    stdout = "garbage output\n"
    controller = FakeController(dumpsys_outputs=[stdout])
    oracle = NotificationPermissionOracle(
        package="com.example.app",
        expected_granted=True,
        require_change_in_window=False,
    )
    ctx = _ctx(controller=controller, episode_dir=tmp_path)
    evidence = oracle.post_check(ctx)
    assert_oracle_event_v0(evidence[0])

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is False
    assert decision["success"] is False


def test_inconclusive_when_permission_not_present(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    stdout = _fixture(
        tests_dir / "fixtures" / "dumpsys_package_com_example_app_permissions_modern.txt"
    )
    controller = FakeController(dumpsys_outputs=[stdout])
    oracle = NotificationPermissionOracle(
        package="com.example.app",
        expected_granted=True,
        require_change_in_window=False,
    )
    ctx = _ctx(controller=controller, episode_dir=tmp_path)
    evidence = oracle.post_check(ctx)

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is False
    assert decision["success"] is False
