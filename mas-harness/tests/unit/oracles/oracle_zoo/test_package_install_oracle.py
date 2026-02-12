from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.dumpsys.package_install import (
    PackageInstallOracle,
    parse_dumpsys_package_output,
)
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256
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
        stdout: str,
        now_device_ms: int,
        tz_offset: str = "+0000",
        returncode: int = 0,
        serial: str = "FAKE_SERIAL",
    ) -> None:
        self.serial = serial
        self._stdout = str(stdout)
        self._returncode = int(returncode)
        self._now_device_ms = int(now_device_ms)
        self._tz_offset = str(tz_offset)

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
        if cmd.startswith("date +%z"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=self._tz_offset,
                stderr="",
                returncode=0,
            )

        if cmd.startswith("dumpsys package "):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=self._stdout,
                stderr="",
                returncode=self._returncode,
            )

        return FakeAdbResult(
            args=["adb", "shell", cmd],
            stdout="",
            stderr="",
            returncode=0,
        )


def _fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _epoch_ms(dt: str) -> int:
    parsed = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _ctx(*, controller: Any, t0_device_ms: int, episode_dir: Path) -> OracleContext:
    episode_time = EpisodeTime(t0_host_utc_ms=0, t0_device_epoch_ms=t0_device_ms, slack_ms=0)
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=episode_time, episode_dir=episode_dir
    )


def _run_oracle(
    *,
    stdout: str,
    now_device_ms: int,
    expected_version_name: Optional[str],
    episode_dir: Path,
) -> list[dict[str, Any]]:
    controller = FakeController(stdout=stdout, now_device_ms=now_device_ms, tz_offset="+0000")
    oracle = PackageInstallOracle(
        package="com.example.app",
        expected_version_name=expected_version_name,
        expected_version_code=123,
        require_last_update_in_window=True,
    )
    t0 = _epoch_ms("2026-01-05 12:00:00")
    return oracle.post_check(_ctx(controller=controller, t0_device_ms=t0, episode_dir=episode_dir))


def test_parse_version_fields() -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    fixture_path = tests_dir / "fixtures" / "dumpsys_package_com_example_app.txt"
    parsed = parse_dumpsys_package_output(_fixture(fixture_path))
    assert parsed["version_name"] == "1.2.3"
    assert parsed["version_code"] == 123
    assert parsed["first_install_time_raw"] == "2026-01-05 12:00:00"
    assert parsed["last_update_time_raw"] == "2026-01-05 12:05:00"


def test_update_time_window(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    good_path = tests_dir / "fixtures" / "dumpsys_package_com_example_app.txt"
    stdout_good = _fixture(good_path)

    now = _epoch_ms("2026-01-05 12:06:00")
    evidence = _run_oracle(
        stdout=stdout_good,
        now_device_ms=now,
        expected_version_name="1.2.3",
        episode_dir=tmp_path,
    )
    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id="package_install")
    assert decision["conclusive"] is True
    assert decision["success"] is True

    preview = evidence[0].get("result_preview") or {}
    assert preview.get("last_update_time_ms") == _epoch_ms("2026-01-05 12:05:00")

    artifacts = evidence[0].get("artifacts")
    assert isinstance(artifacts, list) and artifacts
    artifact = artifacts[0]
    path = tmp_path / str(artifact["path"])
    assert path.exists()
    assert artifact["sha256"] == stable_file_sha256(path)

    old_path = tests_dir / "fixtures" / "dumpsys_package_com_example_app_old.txt"
    stdout_old = _fixture(old_path)
    evidence2 = _run_oracle(
        stdout=stdout_old,
        now_device_ms=now,
        expected_version_name="1.2.3",
        episode_dir=tmp_path,
    )
    decision2 = decision_from_evidence(evidence2, oracle_id="package_install")
    assert decision2["conclusive"] is True
    assert decision2["success"] is False


def test_negative_wrong_version(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    fixture_path = tests_dir / "fixtures" / "dumpsys_package_com_example_app.txt"
    stdout = _fixture(fixture_path)

    now = _epoch_ms("2026-01-05 12:06:00")
    evidence = _run_oracle(
        stdout=stdout,
        now_device_ms=now,
        expected_version_name="9.9.9",
        episode_dir=tmp_path,
    )
    decision = decision_from_evidence(evidence, oracle_id="package_install")
    assert decision["conclusive"] is True
    assert decision["success"] is False
