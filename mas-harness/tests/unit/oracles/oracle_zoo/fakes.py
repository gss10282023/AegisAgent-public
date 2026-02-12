from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from mas_harness.oracles.zoo.base import OracleContext
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
        serial: str = "FAKE_SERIAL",
        now_device_ms: int,
        content_outputs: Mapping[Tuple[str, Optional[str]], str],
    ) -> None:
        self.serial = serial
        self._now_device_ms = int(now_device_ms)
        self._outputs = dict(content_outputs)

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

        if cmd.startswith("content "):
            parts = shlex.split(cmd)
            uri = None
            where = None
            for i, p in enumerate(parts):
                if p == "--uri" and i + 1 < len(parts):
                    uri = parts[i + 1]
                if p == "--where" and i + 1 < len(parts):
                    where = parts[i + 1]
            if uri is None:
                return FakeAdbResult(
                    args=["adb", "shell", cmd],
                    stdout="Error: missing --uri",
                    stderr="",
                    returncode=1,
                )

            stdout = self._outputs.get((uri, where))
            if stdout is None:
                stdout = self._outputs.get((uri, None), "No result found.\n")
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


def make_ctx(
    *,
    controller: Any,
    t0_device_ms: int,
    episode_dir: Any,
    slack_ms: int = 0,
) -> OracleContext:
    episode_time = EpisodeTime(
        t0_host_utc_ms=0,
        t0_device_epoch_ms=int(t0_device_ms),
        slack_ms=int(slack_ms),
    )
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=episode_time, episode_dir=episode_dir
    )
