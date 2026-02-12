from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.chooser import ChooserOracle
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
        returncode: int = 0,
        serial: str = "FAKE_SERIAL",
    ) -> None:
        self.serial = serial
        self._stdout = str(stdout)
        self._returncode = int(returncode)
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

        if cmd.startswith("dumpsys window windows"):
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


def _ctx(*, controller: FakeController, t0_device_ms: int, episode_dir: Path) -> OracleContext:
    episode_time = EpisodeTime(t0_host_utc_ms=0, t0_device_epoch_ms=t0_device_ms, slack_ms=0)
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=episode_time, episode_dir=episode_dir
    )


def test_chooser_oracle_success(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    window_stdout = _fixture(tests_dir / "fixtures" / "dumpsys_window_windows_modern.txt")
    ui_jsonl = _fixture(tests_dir / "fixtures" / "ui_elements_chooser_sample.jsonl")
    (tmp_path / "ui_elements.jsonl").write_text(ui_jsonl, encoding="utf-8")

    t0 = 1_700_000_000_000
    controller = FakeController(stdout=window_stdout, now_device_ms=t0 + 5_000)
    oracle = ChooserOracle(candidate_token="Gmail")
    ctx = _ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)

    evidence = oracle.post_check(ctx)
    for ev in evidence:
        assert_oracle_event_v0(ev)
    decision = decision_from_evidence(evidence, oracle_id="chooser")
    assert decision["conclusive"] is True
    assert decision["success"] is True


def test_chooser_oracle_fail_when_candidate_missing(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    window_stdout = _fixture(tests_dir / "fixtures" / "dumpsys_window_windows_modern.txt")
    ui_jsonl = _fixture(tests_dir / "fixtures" / "ui_elements_chooser_sample.jsonl")
    (tmp_path / "ui_elements.jsonl").write_text(ui_jsonl, encoding="utf-8")

    t0 = 1_700_000_000_000
    controller = FakeController(stdout=window_stdout, now_device_ms=t0 + 5_000)
    oracle = ChooserOracle(candidate_token="NotPresent")
    ctx = _ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)

    decision = decision_from_evidence(oracle.post_check(ctx), oracle_id="chooser")
    assert decision["conclusive"] is True
    assert decision["success"] is False


def test_chooser_oracle_inconclusive_when_window_unparseable(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    ui_jsonl = _fixture(tests_dir / "fixtures" / "ui_elements_chooser_sample.jsonl")
    (tmp_path / "ui_elements.jsonl").write_text(ui_jsonl, encoding="utf-8")

    t0 = 1_700_000_000_000
    controller = FakeController(stdout="unknown window format\n", now_device_ms=t0 + 5_000)
    oracle = ChooserOracle(candidate_token="Gmail")
    ctx = _ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)

    decision = decision_from_evidence(oracle.post_check(ctx), oracle_id="chooser")
    assert decision["conclusive"] is False
    assert decision["success"] is False
