from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.dumpsys.telephony import TelephonyCallStateOracle
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

    def dumpsys(
        self, service: str, *, timeout_s: Optional[float] = None, check: bool = True
    ) -> FakeAdbResult:
        _ = timeout_s, check
        return FakeAdbResult(
            args=["adb", "shell", "dumpsys", str(service)],
            stdout=self._stdout,
            stderr="",
            returncode=self._returncode,
        )


def _ctx(*, controller: Any, episode_dir: Path) -> OracleContext:
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_dir=episode_dir
    )


def test_telephony_call_state_oracle_parses_idle_and_writes_artifact(tmp_path: Path) -> None:
    controller = FakeController(stdout="TelephonyRegistry:\n  mCallState=0\n")
    oracle = TelephonyCallStateOracle(expected="IDLE", timeout_ms=10)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    assert len(evidence) == 1
    assert_oracle_event_v0(evidence[0])

    preview = evidence[0].get("result_preview") or {}
    assert preview.get("call_state") == "IDLE"
    assert preview.get("call_state_code") == 0

    artifacts = evidence[0].get("artifacts")
    assert isinstance(artifacts, list) and len(artifacts) == 1
    artifact = artifacts[0]
    path = tmp_path / str(artifact["path"])
    assert path.exists()
    assert artifact["sha256"] == stable_file_sha256(path)

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True


def test_telephony_call_state_oracle_mismatch_is_conclusive_failure(tmp_path: Path) -> None:
    controller = FakeController(stdout="TelephonyRegistry:\n  mCallState=2\n")
    oracle = TelephonyCallStateOracle(expected="IDLE", timeout_ms=10)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    preview = evidence[0].get("result_preview") or {}
    assert preview.get("call_state") == "OFFHOOK"
    assert preview.get("call_state_code") == 2

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is False


def test_telephony_call_state_oracle_parse_failure_is_inconclusive(tmp_path: Path) -> None:
    controller = FakeController(stdout="TelephonyRegistry:\n  something_else=1\n")
    oracle = TelephonyCallStateOracle(expected="IDLE", timeout_ms=10)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    preview = evidence[0].get("result_preview") or {}
    assert preview.get("call_state") is None

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is False
    assert decision["success"] is False


def test_telephony_call_state_oracle_dumpsys_failure_is_inconclusive(tmp_path: Path) -> None:
    controller = FakeController(stdout="ERROR: dumpsys failed\n", returncode=1)
    oracle = TelephonyCallStateOracle(expected="IDLE", timeout_ms=10)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is False
    assert decision["success"] is False
