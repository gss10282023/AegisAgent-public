from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from mas_harness.phases.phase0_artifacts import Phase0Config, ensure_phase0_artifacts
from mas_harness.runtime.android.controller import AndroidController


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def detect_single_device_serial(*, adb_path: str = "adb") -> str:
    """Return the only connected adb device serial.

    If there are zero or multiple devices, raises SystemExit with guidance.
    """

    try:
        proc = subprocess.run(
            [adb_path, "devices"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except FileNotFoundError as e:
        raise SystemExit(f"adb not found: {adb_path}") from e
    except subprocess.TimeoutExpired as e:
        raise SystemExit(f"adb devices timed out: {adb_path}") from e

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    devices: list[str] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith("*") or line.startswith("List of devices attached"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state == "device":
            devices.append(serial)

    if len(devices) == 1:
        return devices[0]
    if not devices:
        raise SystemExit(
            "No adb devices in state=device; start an emulator or pass "
            "--serial/$MAS_ANDROID_SERIAL."
        )
    raise SystemExit(
        "Multiple adb devices detected; pass --serial or set $MAS_ANDROID_SERIAL. "
        f"devices={devices}"
    )


def run_android_smoke(
    *,
    out_dir: Path,
    serial: str,
    adb_path: str = "adb",
    seed: int = 0,
    timeout_s: float = 30.0,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = out_dir / "screenshots"
    ui_dump_dir = out_dir / "ui_dump"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ui_dump_dir.mkdir(parents=True, exist_ok=True)

    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[4]

    phase0_cfg = Phase0Config(
        execution_mode="android_smoke",
        agent_name="none",
        android_serial=serial,
        adb_path=adb_path,
        reset_strategy=os.environ.get("MAS_RESET_STRATEGY"),
        snapshot_tag=os.environ.get("MAS_SNAPSHOT_TAG"),
    )
    ensure_phase0_artifacts(out_dir=out_dir, repo_root=repo_root, cfg=phase0_cfg, seed=seed)

    controller = AndroidController(adb_path=adb_path, serial=serial, timeout_s=timeout_s)

    screenshot_path = controller.screencap_to_file(
        screenshots_dir / "screenshot_smoke.png", timeout_s=timeout_s
    )
    xml_path = controller.uiautomator_dump(
        local_path=ui_dump_dir / "uiautomator_smoke.xml",
        timeout_s=timeout_s,
    )

    info = {
        "ts_ms": _utc_ms(),
        "serial": serial,
        "adb_path": adb_path,
        "build_fingerprint": controller.get_build_fingerprint() or None,
        "foreground": controller.get_foreground(timeout_s=timeout_s),
        "screen_info": controller.get_screen_info(timeout_s=timeout_s),
        "artifacts": {
            "screenshot": str(screenshot_path.relative_to(out_dir)),
            "uiautomator_xml": str(xml_path.relative_to(out_dir)),
        },
    }
    (out_dir / "android_smoke.json").write_text(_json_dumps(info) + "\n", encoding="utf-8")
    return info


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Android controller smoke probe (Phase2 Step4).")
    parser.add_argument(
        "--out_dir",
        type=Path,
        default=Path("runs/android_smoke"),
        help="Output directory (default: runs/android_smoke)",
    )
    parser.add_argument(
        "--serial",
        type=str,
        default=os.environ.get("MAS_ANDROID_SERIAL"),
        help="adb device serial (default: $MAS_ANDROID_SERIAL)",
    )
    parser.add_argument(
        "--adb_path",
        type=str,
        default=os.environ.get("MAS_ADB_PATH", "adb"),
        help="Path to adb binary (default: adb or $MAS_ADB_PATH)",
    )
    parser.add_argument("--seed", type=int, default=0, help="Seed recorded in run_manifest.json")
    parser.add_argument(
        "--timeout_s",
        type=float,
        default=float(os.environ.get("MAS_ADB_TIMEOUT_S", "30")),
        help="Timeout for individual adb calls (default: 30 or $MAS_ADB_TIMEOUT_S)",
    )
    parser.add_argument(
        "--print_out_dir",
        action="store_true",
        help="Print out_dir only (useful for Makefile targets).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print anything (artifacts are still written).",
    )
    args = parser.parse_args(argv)

    if not args.serial:
        args.serial = detect_single_device_serial(adb_path=str(args.adb_path))

    info = run_android_smoke(
        out_dir=args.out_dir,
        serial=str(args.serial),
        adb_path=str(args.adb_path),
        seed=int(args.seed),
        timeout_s=float(args.timeout_s),
    )
    if args.quiet:
        return 0
    if args.print_out_dir:
        print(str(args.out_dir))
        return 0
    print(_json_dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
