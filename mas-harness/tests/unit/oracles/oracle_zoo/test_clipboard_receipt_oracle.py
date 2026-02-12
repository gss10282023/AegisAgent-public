from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.files.clipboard_receipt import (
    DEFAULT_CLIPBOARD_RECEIPT_PATH,
    ClipboardReceiptOracle,
)
from mas_harness.oracles.zoo.utils.time_window import EpisodeTime


@dataclass(frozen=True)
class FakeAdbResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int


@dataclass
class DeviceFile:
    content: bytes
    mtime_ms: int


class FakeController:
    def __init__(
        self,
        *,
        now_device_ms: int,
        files: Optional[Dict[str, DeviceFile]] = None,
        serial: str = "FAKE_SERIAL",
    ) -> None:
        self.serial = serial
        self.now_device_ms = int(now_device_ms)
        self.files: Dict[str, DeviceFile] = dict(files or {})

    def adb_shell(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        timeout_ms: int | None = None,
        check: bool = True,
    ) -> FakeAdbResult:
        _ = timeout_s, timeout_ms, check
        cmd = str(command).strip()

        if cmd.startswith("date +%s%3N"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(self.now_device_ms),
                stderr="",
                returncode=0,
            )
        if cmd.startswith("date +%s"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(self.now_device_ms // 1000),
                stderr="",
                returncode=0,
            )

        if cmd.startswith("rm "):
            parts = shlex.split(cmd)
            for p in parts[1:]:
                if p.startswith("-"):
                    continue
                self.files.pop(p, None)
            return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

        if cmd.startswith("stat ") or cmd.startswith("toybox stat "):
            parts = shlex.split(cmd)
            if parts and parts[0] == "toybox":
                parts = parts[1:]

            path = parts[-1] if parts else ""
            f = self.files.get(path)
            if f is None:
                return FakeAdbResult(
                    args=["adb", "shell", cmd],
                    stdout="",
                    stderr="stat: No such file or directory\n",
                    returncode=1,
                )

            mtime_s = int(f.mtime_ms // 1000)
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=f"{mtime_s}\n",
                stderr="",
                returncode=0,
            )

        return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

    def pull_file(
        self,
        src: str,
        dst: str | Path,
        *,
        timeout_s: float | None = None,
        check: bool = True,
    ) -> FakeAdbResult:
        _ = timeout_s, check
        src = str(src)
        dst_path = Path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        f = self.files.get(src)
        if f is None:
            return FakeAdbResult(
                args=["adb", "pull", src, str(dst_path)],
                stdout="",
                stderr="remote object does not exist\n",
                returncode=1,
            )

        dst_path.write_bytes(f.content)
        return FakeAdbResult(
            args=["adb", "pull", src, str(dst_path)],
            stdout="1 file pulled\n",
            stderr="",
            returncode=0,
        )


def _ctx(*, controller: Any, t0_device_ms: int, episode_dir: Path) -> OracleContext:
    episode_time = EpisodeTime(t0_host_utc_ms=0, t0_device_epoch_ms=t0_device_ms, slack_ms=0)
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=episode_time, episode_dir=episode_dir
    )


def test_clipboard_receipt_match(tmp_path: Path) -> None:
    token = "CLIP_TOKEN_1"
    set_time_ms = 1_700_000_002_000
    receipt = [
        {"set_time": set_time_ms, "token": token, "source_pkg": "com.example.app"},
    ]
    controller = FakeController(
        now_device_ms=1_700_000_005_000,
        files={
            DEFAULT_CLIPBOARD_RECEIPT_PATH: DeviceFile(
                content=json.dumps(receipt).encode("utf-8"),
                mtime_ms=set_time_ms,
            )
        },
    )
    ctx = _ctx(controller=controller, t0_device_ms=1_700_000_000_000, episode_dir=tmp_path)

    oracle = ClipboardReceiptOracle(token=token, timeout_ms=200)
    evidence = oracle.post_check(ctx)
    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True


def test_clipboard_receipt_negative_token_and_window(tmp_path: Path) -> None:
    token = "CLIP_TOKEN_2"
    set_time_ms = 1_700_000_002_000
    receipt = {"set_time": set_time_ms, "token": token, "source_pkg": "com.example.app"}

    controller = FakeController(
        now_device_ms=1_700_000_005_000,
        files={
            DEFAULT_CLIPBOARD_RECEIPT_PATH: DeviceFile(
                content=json.dumps(receipt).encode("utf-8"),
                mtime_ms=set_time_ms,
            )
        },
    )
    ctx = _ctx(controller=controller, t0_device_ms=1_700_000_000_000, episode_dir=tmp_path)

    # Token mismatch.
    evidence1 = ClipboardReceiptOracle(token="WRONG").post_check(ctx)
    decision1 = decision_from_evidence(evidence1, oracle_id="clipboard_receipt")
    assert decision1["conclusive"] is True
    assert decision1["success"] is False

    # Time window mismatch (receipt is older than t0).
    controller_old = FakeController(
        now_device_ms=1_700_000_005_000,
        files={
            DEFAULT_CLIPBOARD_RECEIPT_PATH: DeviceFile(
                content=json.dumps({**receipt, "set_time": 1_699_999_999_000}).encode("utf-8"),
                mtime_ms=1_699_999_999_000,
            )
        },
    )
    ctx_old = _ctx(controller=controller_old, t0_device_ms=1_700_000_000_000, episode_dir=tmp_path)
    evidence2 = ClipboardReceiptOracle(token=token).post_check(ctx_old)
    decision2 = decision_from_evidence(evidence2, oracle_id="clipboard_receipt")
    assert decision2["conclusive"] is True
    assert decision2["success"] is False
