from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.dumpsys.window import WindowOracle, parse_window_focus
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


def _ctx(*, controller: Any, t0_device_ms: int, episode_dir: Path) -> OracleContext:
    episode_time = EpisodeTime(t0_host_utc_ms=0, t0_device_epoch_ms=t0_device_ms, slack_ms=0)
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=episode_time, episode_dir=episode_dir
    )


def test_parse_window_focus() -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    legacy = _fixture(tests_dir / "fixtures" / "dumpsys_window_windows_legacy.txt")
    modern = _fixture(tests_dir / "fixtures" / "dumpsys_window_windows_modern.txt")

    parsed1 = parse_window_focus(legacy)
    assert parsed1["ok"] is True
    assert parsed1["package"] == "com.example.app"
    assert parsed1["activity"] == "com.example.app.MainActivity"

    parsed2 = parse_window_focus(modern)
    assert parsed2["ok"] is True
    assert parsed2["package"] == "com.android.intentresolver"
    assert parsed2["activity"] == "com.android.intentresolver.ChooserActivity"


def _run_oracle(
    *,
    stdout: str,
    now_device_ms: int,
    token: str,
    episode_dir: Path,
) -> list[dict[str, Any]]:
    controller = FakeController(stdout=stdout, now_device_ms=now_device_ms)
    oracle = WindowOracle(token=token, timeout_ms=100)
    ctx = _ctx(controller=controller, t0_device_ms=1_700_000_000_000, episode_dir=episode_dir)
    return oracle.post_check(ctx)


def test_match_window_token(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    stdout = _fixture(tests_dir / "fixtures" / "dumpsys_window_windows_modern.txt")

    evidence = _run_oracle(
        stdout=stdout,
        now_device_ms=1_700_000_005_000,
        token="ChooserActivity",
        episode_dir=tmp_path,
    )
    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id="window")
    assert decision["conclusive"] is True
    assert decision["success"] is True

    artifacts = evidence[0].get("artifacts")
    assert isinstance(artifacts, list) and artifacts
    artifact = artifacts[0]
    path = tmp_path / str(artifact["path"])
    assert path.exists()
    assert artifact["sha256"] == stable_file_sha256(path)

    queries = evidence[0].get("queries")
    assert isinstance(queries, list) and queries
    assert any(
        isinstance(q, dict) and q.get("cmd") == "shell dumpsys window windows" for q in queries
    )


def test_inconclusive_on_unknown_format(tmp_path: Path) -> None:
    evidence = _run_oracle(
        stdout="some new android format without focus/window entries\n",
        now_device_ms=1_700_000_005_000,
        token="ChooserActivity",
        episode_dir=tmp_path,
    )
    decision = decision_from_evidence(evidence, oracle_id="window")
    assert decision["conclusive"] is False
    assert decision["success"] is False
    assert "parse" in decision["reason"].lower()
