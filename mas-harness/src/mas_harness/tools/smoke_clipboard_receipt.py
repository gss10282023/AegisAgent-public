from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from mas_harness.oracles.zoo.base import OracleContext, decision_from_evidence
from mas_harness.oracles.zoo.files.clipboard_receipt import ClipboardReceiptOracle
from mas_harness.oracles.zoo.utils.time_window import capture_episode_time
from mas_harness.runtime.android.controller import AndroidController

REPO_ROOT = Path(__file__).resolve().parents[4]

APP_ID = "com.mas.clipboardreceipt"
IME_ID = f"{APP_ID}/.ReceiptImeService"
RECEIPT_PATH = f"/sdcard/Android/data/{APP_ID}/files/clipboard_receipt.json"


def _run(
    cmd: list[str], *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        check=check,
        capture_output=True,
        text=True,
    )


def _pick_serial(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("ANDROID_SERIAL")
    if env:
        return env
    res = _run(["adb", "devices"])
    devices: list[str] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        if "\tdevice" in line:
            devices.append(line.split("\t", 1)[0])
    if len(devices) == 1:
        return devices[0]
    raise SystemExit(f"ambiguous adb devices: {devices} (pass --serial or set ANDROID_SERIAL)")


def _ensure_gradle(*, version: str, cache_dir: Path) -> Path:
    gradle_home = cache_dir / f"gradle-{version}"
    gradle_bin = gradle_home / "bin" / "gradle"
    if gradle_bin.exists():
        return gradle_bin

    cache_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://services.gradle.org/distributions/gradle-{version}-bin.zip"
    zip_path = cache_dir / f"gradle-{version}-bin.zip"
    print(f"[smoke] downloading Gradle: {url}")
    urllib.request.urlretrieve(url, zip_path)  # noqa: S310 - controlled URL
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(cache_dir)
    if not gradle_bin.exists():
        raise RuntimeError(f"gradle binary missing after extract: {gradle_bin}")
    return gradle_bin


def _build_companion_app(*, gradle_bin: Path) -> Path:
    project_dir = REPO_ROOT / "android" / "companion-apps" / "clipboard_receipt"
    apk = project_dir / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    _run([str(gradle_bin), "-p", str(project_dir), ":app:assembleDebug"], check=True)
    if not apk.exists():
        raise RuntimeError(f"APK not found: {apk}")
    return apk


def _install_and_enable_ime(*, serial: str, apk: Path) -> None:
    _run(["adb", "-s", serial, "install", "-r", "-t", str(apk)], check=True)
    _run(["adb", "-s", serial, "shell", "ime", "enable", IME_ID], check=True)
    _run(["adb", "-s", serial, "shell", "ime", "set", IME_ID], check=True)

    # Bring a text field to foreground so the IME service starts.
    _run(
        ["adb", "-s", serial, "shell", "am", "start", "-n", f"{APP_ID}/{APP_ID}.MainActivity"],
        check=False,
    )


def _run_oracle_smoke(*, serial: str, token: str, slack_ms: int) -> int:
    controller = AndroidController(serial=serial)
    episode_time, _ = capture_episode_time(
        controller=controller, task_spec={"time_window": {"slack_ms": slack_ms}}
    )
    episode_dir = Path(tempfile.mkdtemp(prefix="mas_smoke_clipboard_receipt_"))
    ctx = OracleContext.from_task_and_controller(
        task_spec={},
        controller=controller,
        episode_time=episode_time,
        episode_dir=episode_dir,
    )

    oracle = ClipboardReceiptOracle(
        token=token,
        remote_path=RECEIPT_PATH,
        clear_before_run=True,
        timeout_ms=15_000,
    )

    pre = oracle.pre_check(ctx)
    pre_decision = decision_from_evidence(pre, oracle_id=oracle.oracle_id, phase="pre")
    print("[smoke] pre:", pre_decision)

    controller.adb_shell(
        "am broadcast -a com.mas.clipboardreceipt.SET_CLIP "
        f"-n {APP_ID}/{APP_ID}.SetClipboardReceiver --es text {token}",
        check=False,
    )
    time.sleep(2)

    post = oracle.post_check(ctx)
    post_decision = decision_from_evidence(post, oracle_id=oracle.oracle_id)
    print("[smoke] post:", post_decision)

    return 0 if bool(post_decision.get("success")) else 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--serial", default=None, help="adb device serial (or set ANDROID_SERIAL)")
    p.add_argument("--token", default="CLIP_TOKEN_SMOKE_1", help="clipboard token to set")
    p.add_argument("--slack-ms", type=int, default=0, help="episode time-window slack (ms)")
    p.add_argument(
        "--gradle-version",
        default="8.7",
        help="Gradle distribution version to download",
    )
    p.add_argument(
        "--skip-build",
        action="store_true",
        help="skip building/installing the companion app",
    )
    args = p.parse_args()

    serial = _pick_serial(args.serial)
    print(f"[smoke] serial={serial}")

    if not args.skip_build:
        gradle_bin = _ensure_gradle(
            version=str(args.gradle_version),
            cache_dir=REPO_ROOT / "tools" / "gradle_dist",
        )
        apk = _build_companion_app(gradle_bin=gradle_bin)
        _install_and_enable_ime(serial=serial, apk=apk)
        time.sleep(1)

    return _run_oracle_smoke(serial=serial, token=str(args.token), slack_ms=int(args.slack_ms))


if __name__ == "__main__":
    raise SystemExit(main())
