from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, Tuple

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.dumpsys.appops import (
    AppOpCheck,
    AppOpsOracle,
    parse_appops_get_output,
)
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
        appops_outputs: Sequence[Tuple[str, int]],
        dumpsys_package_stdout: str,
        now_device_ms: int,
        serial: str = "FAKE_SERIAL",
    ) -> None:
        self.serial = serial
        self._appops_outputs = list(appops_outputs)
        self._appops_calls = 0
        self._dumpsys_package_stdout = str(dumpsys_package_stdout)
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

        if cmd.startswith("appops get "):
            idx = min(self._appops_calls, len(self._appops_outputs) - 1)
            stdout, rc = self._appops_outputs[idx] if self._appops_outputs else ("", 0)
            self._appops_calls += 1
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=stdout,
                stderr="",
                returncode=int(rc),
            )

        if cmd.startswith("dumpsys package "):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=self._dumpsys_package_stdout,
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


def test_parse_appops() -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    legacy = _fixture(tests_dir / "fixtures" / "appops_get_com_example_app_legacy.txt")
    modern = _fixture(tests_dir / "fixtures" / "appops_get_com_example_app_modern.txt")

    parsed_legacy = parse_appops_get_output(legacy)
    assert parsed_legacy["ok"] is True
    ops = parsed_legacy["ops"]
    assert ops["COARSE_LOCATION"]["scopes"]["uid"] == "ignore"
    assert ops["COARSE_LOCATION"]["scopes"]["package"] == "ignore"
    assert ops["RUN_IN_BACKGROUND"]["scopes"]["package"] == "allow"

    parsed_modern = parse_appops_get_output(modern)
    assert parsed_modern["ok"] is True
    ops2 = parsed_modern["ops"]
    assert ops2["COARSE_LOCATION"]["scopes"]["uid"] == "allow"
    assert ops2["FINE_LOCATION"]["scopes"]["package"] == "foreground"


def test_match_op_state(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    stdout_pre = _fixture(tests_dir / "fixtures" / "appops_get_com_example_app_legacy.txt")
    stdout_post = stdout_pre.replace("COARSE_LOCATION: ignore", "COARSE_LOCATION: allow")
    dumpsys_pkg = _fixture(
        tests_dir / "fixtures" / "dumpsys_package_com_example_app_permissions_modern.txt"
    )

    t0 = 1_700_000_000_000
    now = t0 + 10_000
    controller = FakeController(
        appops_outputs=[(stdout_pre, 0), (stdout_post, 0)],
        dumpsys_package_stdout=dumpsys_pkg,
        now_device_ms=now,
    )
    oracle = AppOpsOracle(
        package="com.example.app",
        checks=[
            AppOpCheck(
                op="COARSE_LOCATION",
                expected_any_of=("allow",),
                scope="package",
                require_change_in_window=True,
            )
        ],
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
    op_modes = preview.get("op_modes") or {}
    assert op_modes.get("COARSE_LOCATION:package") == "allow"


def test_inconclusive_when_command_unavailable(tmp_path: Path) -> None:
    stdout = "/system/bin/sh: appops: not found\n"
    tests_dir = Path(__file__).resolve().parents[3]
    dumpsys_pkg = _fixture(
        tests_dir / "fixtures" / "dumpsys_package_com_example_app_permissions_modern.txt"
    )

    t0 = 1_700_000_000_000
    now = t0 + 10_000
    controller = FakeController(
        appops_outputs=[(stdout, 127)],
        dumpsys_package_stdout=dumpsys_pkg,
        now_device_ms=now,
    )
    oracle = AppOpsOracle(
        package="com.example.app",
        checks=[
            AppOpCheck(
                op="COARSE_LOCATION",
                expected_any_of=("allow",),
                scope="any",
                require_change_in_window=False,
            )
        ],
    )

    ctx = _ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)
    evidence = oracle.post_check(ctx)
    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is False
    assert decision["success"] is False
