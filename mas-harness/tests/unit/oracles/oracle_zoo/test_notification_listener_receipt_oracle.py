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
from mas_harness.oracles.zoo.files.notification_listener_receipt import (
    DEFAULT_NOTIFICATION_RECEIPT_PATH,
    NotificationListenerReceiptOracle,
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


def test_notification_listener_receipt_match(tmp_path: Path) -> None:
    token = "NOTIF_TOKEN_1"
    pkg = "com.example.app"
    post_time_ms = 1_700_000_002_000

    receipt = [
        {
            "pkg": pkg,
            "title": f"Hello {token}",
            "text": "World",
            "post_time": post_time_ms,
            "token_hit": token,
        }
    ]
    controller = FakeController(
        now_device_ms=1_700_000_005_000,
        files={
            DEFAULT_NOTIFICATION_RECEIPT_PATH: DeviceFile(
                content=json.dumps(receipt).encode("utf-8"),
                mtime_ms=post_time_ms,
            )
        },
    )
    ctx = _ctx(controller=controller, t0_device_ms=1_700_000_000_000, episode_dir=tmp_path)

    oracle = NotificationListenerReceiptOracle(package=pkg, token=token, timeout_ms=200)
    evidence = oracle.post_check(ctx)
    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True

    artifacts = evidence[0].get("artifacts")
    assert isinstance(artifacts, list) and artifacts
    artifact_path = tmp_path / str(artifacts[0]["path"])
    assert artifact_path.exists()


def test_notification_listener_receipt_negative_token_pkg_window(tmp_path: Path) -> None:
    token = "NOTIF_TOKEN_2"
    pkg = "com.example.app"
    post_time_ms = 1_700_000_002_000

    receipt = {
        "pkg": pkg,
        "title": "Hello",
        "text": "World",
        "post_time": post_time_ms,
        "token_hit": token,
    }
    controller = FakeController(
        now_device_ms=1_700_000_005_000,
        files={
            DEFAULT_NOTIFICATION_RECEIPT_PATH: DeviceFile(
                content=json.dumps(receipt).encode("utf-8"),
                mtime_ms=post_time_ms,
            )
        },
    )
    ctx = _ctx(controller=controller, t0_device_ms=1_700_000_000_000, episode_dir=tmp_path)

    # Token mismatch.
    evidence1 = NotificationListenerReceiptOracle(package=pkg, token="WRONG").post_check(ctx)
    decision1 = decision_from_evidence(evidence1, oracle_id="notification_listener_receipt")
    assert decision1["conclusive"] is True
    assert decision1["success"] is False

    # Package mismatch.
    evidence2 = NotificationListenerReceiptOracle(package="com.wrong.app", token=token).post_check(
        ctx
    )
    decision2 = decision_from_evidence(evidence2, oracle_id="notification_listener_receipt")
    assert decision2["conclusive"] is True
    assert decision2["success"] is False

    # Time window mismatch (receipt is older than t0).
    controller_old = FakeController(
        now_device_ms=1_700_000_005_000,
        files={
            DEFAULT_NOTIFICATION_RECEIPT_PATH: DeviceFile(
                content=json.dumps({**receipt, "post_time": 1_699_999_999_000}).encode("utf-8"),
                mtime_ms=1_699_999_999_000,
            )
        },
    )
    ctx_old = _ctx(controller=controller_old, t0_device_ms=1_700_000_000_000, episode_dir=tmp_path)
    evidence3 = NotificationListenerReceiptOracle(package=pkg, token=token).post_check(ctx_old)
    decision3 = decision_from_evidence(evidence3, oracle_id="notification_listener_receipt")
    assert decision3["conclusive"] is True
    assert decision3["success"] is False


def test_notification_listener_receipt_precheck_clears(tmp_path: Path) -> None:
    token = "NOTIF_TOKEN_3"
    pkg = "com.example.app"

    controller = FakeController(
        now_device_ms=1_700_000_005_000,
        files={
            DEFAULT_NOTIFICATION_RECEIPT_PATH: DeviceFile(
                content=b"{}",
                mtime_ms=1_700_000_000_000,
            )
        },
    )
    ctx = _ctx(controller=controller, t0_device_ms=1_700_000_000_000, episode_dir=tmp_path)

    oracle = NotificationListenerReceiptOracle(package=pkg, token=token, timeout_ms=200)
    evidence = oracle.pre_check(ctx)
    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id, phase="pre")
    assert decision["success"] is True
    assert DEFAULT_NOTIFICATION_RECEIPT_PATH not in controller.files
