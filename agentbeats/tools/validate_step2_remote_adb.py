from __future__ import annotations

import argparse
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Compose:
    files: list[str]

    def _cmd(self) -> list[str]:
        cmd = ["docker", "compose"]
        for file in self.files:
            cmd.extend(["-f", file])
        return cmd

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self._cmd(), *args],
            check=check,
            text=True,
            capture_output=False,
        )

    def output(self, *args: str) -> str:
        res = subprocess.run(
            [*self._cmd(), *args],
            check=True,
            text=True,
            capture_output=True,
        )
        return res.stdout.strip()


def _repo_root() -> Path:
    # agentbeats/tools/<this_file.py>
    return Path(__file__).resolve().parents[2]


def _run_host_script(*, script: Path, env: dict[str, str]) -> None:
    if not script.exists():
        raise FileNotFoundError(str(script))
    subprocess.run(
        ["bash", str(script)],
        check=True,
        text=True,
        env={**os.environ, **env},
    )


def _wait_for_serial_absent(*, serial: str, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = subprocess.run(["adb", "devices"], text=True, capture_output=True, check=False)
        out = (res.stdout or "").splitlines()
        # skip header
        present = any(line.split()[:1] == [serial] for line in out[1:])
        if not present:
            return
        time.sleep(1)
    raise TimeoutError(f"timed out waiting for {serial} to disappear from `adb devices`")


def _wait_for_container_adb(compose: Compose, *, service: str, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = subprocess.run(
            [*compose._cmd(), "exec", "-T", service, "sh", "-lc", "command -v adb >/dev/null"],
            text=True,
            capture_output=True,
            check=False,
        )
        if res.returncode == 0:
            return
        time.sleep(1)
    raise TimeoutError(f"timed out waiting for adb to be installed in service={service}")


def _container_check(
    compose: Compose,
    *,
    service: str,
    adb_host: str,
    adb_port: int,
    android_serial: str,
) -> None:
    devices = subprocess.run(
        [
            *compose._cmd(),
            "exec",
            "-T",
            service,
            "adb",
            "-H",
            adb_host,
            "-P",
            str(adb_port),
            "devices",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout

    serial_ok = False
    for line in (devices or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == android_serial and parts[1] == "device":
            serial_ok = True
            break
    if not serial_ok:
        raise RuntimeError(
            f"[{service}] expected `{android_serial}\\tdevice` in `adb devices` output:\n{devices}"
        )

    boot = subprocess.run(
        [
            *compose._cmd(),
            "exec",
            "-T",
            service,
            "adb",
            "-H",
            adb_host,
            "-P",
            str(adb_port),
            "-s",
            android_serial,
            "shell",
            "getprop",
            "sys.boot_completed",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout

    if (boot or "").strip().strip("\r") != "1":
        raise RuntimeError(f"[{service}] sys.boot_completed != 1 (got: {boot!r})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Step 2 verifier: host-run emulator + host-run ADB server; "
            "containers can stably control the device via remote adb server."
        )
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        default=None,
        help="docker compose file path (repeatable; later files override earlier ones)",
    )
    parser.add_argument(
        "--restarts",
        type=int,
        default=2,
        help="number of consecutive emulator restarts to verify",
    )
    parser.add_argument(
        "--adb-host",
        type=str,
        default="host.docker.internal",
        help="ADB server host reachable from containers",
    )
    parser.add_argument(
        "--adb-port",
        type=int,
        default=5037,
        help="ADB server port reachable from containers",
    )
    parser.add_argument(
        "--avd-name",
        type=str,
        default=None,
        help="Host AVD name to start (sets AVD_NAME for emulator_host scripts)",
    )
    parser.add_argument(
        "--console-port",
        type=int,
        default=5554,
        help="Emulator console port (determines default android serial)",
    )
    parser.add_argument(
        "--android-serial",
        type=str,
        default=None,
        help="Android device serial to target (default: emulator-<console-port>)",
    )
    parser.add_argument(
        "--skip-host-setup",
        action="store_true",
        help="Skip running agentbeats/emulator_host scripts (assume host is already ready)",
    )
    parser.add_argument(
        "--boot-timeout-s",
        type=int,
        default=900,
        help="Host boot timeout for wait_until_ready.sh (per start)",
    )
    parser.add_argument(
        "--container-timeout-s",
        type=int,
        default=120,
        help="Timeout waiting for adb to be installed in containers",
    )
    parser.add_argument(
        "--serial-absent-timeout-s",
        type=int,
        default=60,
        help="Timeout waiting for emulator to fully stop (per restart)",
    )
    args = parser.parse_args()

    if args.skip_host_setup and args.restarts != 0:
        print("[step2] ERROR: --skip-host-setup requires --restarts 0.")
        return 2

    android_serial = args.android_serial or f"emulator-{args.console_port}"

    compose_files = args.compose_file
    if not compose_files:
        compose_files = ["agentbeats/scenarios/phase1_remote_adb_smoke/compose.yaml"]
        if platform.system().lower() == "linux":
            compose_files.append("agentbeats/scenarios/phase1_remote_adb_smoke/compose.linux.yaml")
    compose = Compose(files=compose_files)

    repo_root = _repo_root()
    host_scripts = repo_root / "agentbeats" / "emulator_host"
    env: dict[str, str] = {}

    if args.avd_name:
        env["AVD_NAME"] = args.avd_name
    env["EMULATOR_CONSOLE_PORT"] = str(args.console_port)
    env["ANDROID_SERIAL"] = android_serial

    try:
        subprocess.run(["adb", "version"], check=True, text=True, capture_output=False)
    except FileNotFoundError:
        print("[step2] ERROR: `adb` not found on host PATH.")
        print("[step2] Install Android Platform Tools, then re-run.")
        return 2

    if not args.skip_host_setup:
        _run_host_script(
            script=host_scripts / "start_adb_server.sh",
            env={**env, **dict(ADB_PORT=str(args.adb_port))},
        )
        _run_host_script(
            script=host_scripts / "start_emulator.sh",
            env=env,
        )
        _run_host_script(
            script=host_scripts / "wait_until_ready.sh",
            env={**env, **dict(BOOT_TIMEOUT_S=str(args.boot_timeout_s))},
        )

    compose.run("down", "-v", "--remove-orphans", check=False)
    try:
        compose.run("up", "-d", "green", "purple")
        _wait_for_container_adb(compose, service="green", timeout_s=args.container_timeout_s)
        _wait_for_container_adb(compose, service="purple", timeout_s=args.container_timeout_s)

        total_cycles = args.restarts + 1
        for i in range(total_cycles):
            cycle = i + 1
            print(f"[step2] cycle {cycle}/{total_cycles}: container -> host ADB server -> emulator")
            _container_check(
                compose,
                service="green",
                adb_host=args.adb_host,
                adb_port=args.adb_port,
                android_serial=android_serial,
            )
            _container_check(
                compose,
                service="purple",
                adb_host=args.adb_host,
                adb_port=args.adb_port,
                android_serial=android_serial,
            )

            if i < args.restarts:
                print(f"[step2] restarting emulator (serial={android_serial})...")
                subprocess.run(
                    ["adb", "-s", android_serial, "emu", "kill"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                _wait_for_serial_absent(
                    serial=android_serial, timeout_s=args.serial_absent_timeout_s
                )

                if not args.skip_host_setup:
                    _run_host_script(
                        script=host_scripts / "start_emulator.sh",
                        env=env,
                    )
                    _run_host_script(
                        script=host_scripts / "wait_until_ready.sh",
                        env={**env, **dict(BOOT_TIMEOUT_S=str(args.boot_timeout_s))},
                    )

        print("[step2] OK: containers can stably control host emulator via remote ADB server")
        return 0
    finally:
        compose.run("down", "-v", "--remove-orphans", check=False)


if __name__ == "__main__":
    raise SystemExit(main())
