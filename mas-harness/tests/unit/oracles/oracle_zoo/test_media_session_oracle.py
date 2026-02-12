from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.dumpsys.media_session import (
    MediaSessionOracle,
    parse_media_sessions,
)
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256


@dataclass(frozen=True)
class FakeAdbResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int


class FakeController:
    def __init__(self, *, stdout: str, returncode: int = 0, serial: str = "FAKE_SERIAL") -> None:
        self.serial = serial
        self._stdout = str(stdout)
        self._returncode = int(returncode)

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
        if cmd.startswith("dumpsys media_session"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=self._stdout,
                stderr="",
                returncode=self._returncode,
            )
        return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)


def _fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ctx(*, controller: Any, episode_dir: Path) -> OracleContext:
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_dir=episode_dir
    )


def test_parse_media_sessions_extracts_package_state_and_metadata() -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    stdout = _fixture(tests_dir / "fixtures" / "dumpsys_media_session_playing.txt")

    parsed = parse_media_sessions(stdout)
    assert parsed["ok"] is True
    sessions = parsed["sessions"]
    assert isinstance(sessions, list) and sessions

    first = sessions[0]
    assert first["package"] == "com.example.music"
    assert first["playback_state"] == "PLAYING"
    assert first["playback_state_code"] == 3

    metadata = first["metadata"]
    assert isinstance(metadata, dict)
    assert "MEDIA_TOKEN_123" in str(metadata.get("TITLE", ""))


def test_media_session_oracle_matches_playing_token_and_writes_artifact(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    stdout = _fixture(tests_dir / "fixtures" / "dumpsys_media_session_playing.txt")
    controller = FakeController(stdout=stdout)

    oracle = MediaSessionOracle(
        token="MEDIA_TOKEN_123",
        package="com.example.music",
        expected_states="PLAYING",
        timeout_ms=50,
    )
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    assert len(evidence) == 1
    assert_oracle_event_v0(evidence[0])

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
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
        isinstance(q, dict) and q.get("cmd") == "shell dumpsys media_session" for q in queries
    )


def test_media_session_oracle_negative_wrong_playback_state(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    stdout = _fixture(tests_dir / "fixtures" / "dumpsys_media_session_paused.txt")
    controller = FakeController(stdout=stdout)

    oracle = MediaSessionOracle(
        token="MEDIA_TOKEN_123",
        package="com.example.music",
        expected_states="PLAYING",
        timeout_ms=50,
    )
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is False
