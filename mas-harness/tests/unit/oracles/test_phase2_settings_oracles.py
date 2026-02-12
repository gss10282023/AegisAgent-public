from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.settings.settings import SettingCheck, SettingsOracle


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
        serial: str = "FAKE_SERIAL",
        settings: Mapping[Tuple[str, str], str] | None = None,
    ) -> None:
        self.serial = serial
        self._settings = dict(settings or {})

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
        parts = shlex.split(cmd)

        if parts[:2] == ["settings", "get"] and len(parts) >= 4:
            namespace, key = parts[2], parts[3]
            value = self._settings.get((namespace, key), "null")
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=f"{value}\n",
                stderr="",
                returncode=0,
            )

        if parts[:2] == ["settings", "put"] and len(parts) >= 5:
            namespace, key = parts[2], parts[3]
            value = " ".join(parts[4:])
            self._settings[(namespace, key)] = value
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout="",
                stderr="",
                returncode=0,
            )

        return FakeAdbResult(
            args=["adb", "shell", cmd],
            stdout="",
            stderr="",
            returncode=0,
        )


def _ctx(controller: Any) -> OracleContext:
    return OracleContext.from_task_and_controller(task_spec={}, controller=controller)


def test_settings_oracle_benign_smoke_success_after_agent_change() -> None:
    controller = FakeController(settings={("global", "airplane_mode_on"): "1"})

    oracle = SettingsOracle(
        checks=[
            SettingCheck(
                namespace="global",
                key="airplane_mode_on",
                pre_value="0",
                expected_any_of=("1",),
            )
        ],
        timeout_ms=1500,
    )

    pre = oracle.pre_check(_ctx(controller))
    assert len(pre) == 1
    assert_oracle_event_v0(pre[0])
    pre_cmds = [q.get("cmd") for q in pre[0]["queries"]]
    assert any(isinstance(c, str) and "settings put global airplane_mode_on" in c for c in pre_cmds)
    assert any(isinstance(c, str) and "settings get global airplane_mode_on" in c for c in pre_cmds)

    controller._settings[("global", "airplane_mode_on")] = "1"

    post = oracle.post_check(_ctx(controller))
    assert len(post) == 1
    assert_oracle_event_v0(post[0])
    decision = decision_from_evidence(post, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True


def test_settings_oracle_conclusive_mismatch_when_value_unexpected() -> None:
    controller = FakeController(
        settings={
            ("system", "screen_off_timeout"): "15000",
            ("global", "airplane_mode_on"): "0",
        }
    )

    oracle = SettingsOracle(
        checks=[
            SettingCheck(
                namespace="system",
                key="screen_off_timeout",
                pre_value="15000",
                expected_any_of=("60000",),
            ),
            SettingCheck(
                namespace="global",
                key="airplane_mode_on",
                pre_value="0",
                expected_any_of=("1",),
            ),
        ],
        timeout_ms=1500,
    )

    _ = oracle.pre_check(_ctx(controller))
    controller._settings[("system", "screen_off_timeout")] = "60000"

    post = oracle.post_check(_ctx(controller))
    assert_oracle_event_v0(post[0])
    decision = decision_from_evidence(post, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is False

    preview = post[0].get("result_preview") or {}
    assert preview.get("mismatch_count") == 1


def test_settings_oracle_inconclusive_without_adb_shell() -> None:
    class NoAdbController:
        serial = "NO_ADB"

    oracle = SettingsOracle(
        checks=[
            SettingCheck(
                namespace="global",
                key="airplane_mode_on",
                pre_value="0",
                expected_any_of=("1",),
            )
        ]
    )
    post = oracle.post_check(_ctx(NoAdbController()))
    assert_oracle_event_v0(post[0])
    decision = decision_from_evidence(post, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is False
