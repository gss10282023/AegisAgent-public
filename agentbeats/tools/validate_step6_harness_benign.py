from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


def _repo_root() -> Path:
    # agentbeats/tools/<this_file.py>
    return Path(__file__).resolve().parents[2]


def _venv_python(repo_root: Path) -> str:
    candidates = [
        repo_root / ".venv" / "bin" / "python",
        repo_root / ".venv" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def _parse_adb_server(raw: str) -> tuple[str, int] | None:
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("tcp:"):
        s = s.removeprefix("tcp:").strip()
    if not s:
        return None

    if s.startswith("["):
        # Best-effort IPv6: [::1]:5037
        if "]" not in s:
            return None
        host, rest = s[1:].split("]", 1)
        host = host.strip()
        rest = rest.strip()
        if not host:
            return None
        if rest.startswith(":"):
            rest = rest[1:]
        port_str = rest.strip() or "5037"
    else:
        if ":" in s:
            host, port_str = s.rsplit(":", 1)
            host = host.strip()
            port_str = port_str.strip()
        else:
            host, port_str = s.strip(), "5037"

    if not host:
        return None
    try:
        port = int(port_str)
    except Exception:
        return None
    if port <= 0 or port > 65535:
        return None
    return host, port


def _resolve_default_adb_server() -> str:
    raw = os.environ.get("MAS_ADB_SERVER")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    raw = os.environ.get("ADB_SERVER_SOCKET")
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if s.startswith("tcp:"):
            s = s.removeprefix("tcp:")
        return s.strip()

    host = os.environ.get("ADB_HOST")
    port = os.environ.get("ADB_PORT")
    if host and port:
        return f"{host.strip()}:{port.strip()}"

    return "127.0.0.1:5037"


def _tail(path: Path, *, lines: int = 200) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(data) <= lines:
        return "\n".join(data)
    return "\n".join(data[-lines:])


def _pick_free_port(*, host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        s.listen(1)
        return int(s.getsockname()[1])


def _run_host_script(*, script: Path, env: dict[str, str]) -> None:
    subprocess.run(
        ["bash", str(script)],
        check=True,
        text=True,
        env={**os.environ, **env},
    )


def _adb_check(*, host: str, port: int, serial: str) -> None:
    devices = subprocess.run(
        ["adb", "-H", host, "-P", str(port), "devices"],
        check=False,
        text=True,
        capture_output=True,
    )
    if devices.returncode != 0:
        raise RuntimeError(f"`adb devices` failed: {devices.stdout}\n{devices.stderr}")

    boot = subprocess.run(
        [
            "adb",
            "-H",
            host,
            "-P",
            str(port),
            "-s",
            serial,
            "shell",
            "getprop",
            "sys.boot_completed",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if boot.returncode != 0:
        raise RuntimeError(f"`adb shell getprop` failed: {boot.stdout}\n{boot.stderr}")
    if (boot.stdout or "").strip().strip("\r") != "1":
        raise RuntimeError(f"sys.boot_completed != 1 (got: {boot.stdout!r})")


def _wait_for_agent_card(*, base_url: str, timeout_s: int) -> None:
    try:
        import httpx
    except Exception as e:  # pragma: no cover
        raise RuntimeError("httpx is required (install agentbeats/requirements.txt)") from e

    url = base_url.rstrip("/") + "/.well-known/agent-card.json"
    deadline = time.time() + timeout_s
    last_err: str | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code == 200:
                return
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:  # pragma: no cover
            last_err = str(e)
        time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for agent card: {url} ({last_err})")


def _event_parts(event: object) -> list[Any]:
    try:
        from a2a.types import Message, Part
    except Exception as e:  # pragma: no cover
        raise RuntimeError("a2a-sdk is required (install agentbeats/requirements.txt)") from e

    parts: list[Part] = []
    match event:
        case Message() as msg:
            parts.extend(msg.parts or [])
        case (task, _update):
            status_msg = getattr(getattr(task, "status", None), "message", None)
            if status_msg:
                parts.extend(status_msg.parts or [])
            for artifact in getattr(task, "artifacts", None) or []:
                parts.extend(artifact.parts or [])
        case _:
            pass
    return parts


def _extract_results_artifact(event: object) -> dict[str, Any]:
    try:
        from a2a.types import DataPart
    except Exception as e:  # pragma: no cover
        raise RuntimeError("a2a-sdk is required (install agentbeats/requirements.txt)") from e

    for part in _event_parts(event):
        if not isinstance(part.root, DataPart):
            continue
        if not isinstance(part.root.data, dict):
            continue
        data = part.root.data
        if data.get("schema_version") == "agentbeats.phase1.results.v1":
            return data
    raise RuntimeError("Green response did not include a results.json DataPart")


def _print_event_progress(event: object) -> None:
    try:
        from a2a.types import TextPart
    except Exception:  # pragma: no cover
        return

    for part in _event_parts(event):
        if isinstance(part.root, TextPart):
            text = str(part.root.text or "").strip()
            if text:
                print(f"[step6] {text}")


async def _send_assessment(
    *,
    green_url: str,
    purple_url: str,
    config: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    try:
        import httpx
        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.types import Message, Part, Role, TextPart
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "a2a-sdk/httpx is required (install agentbeats/requirements.txt and run via .venv)"
        ) from e

    payload = {"participants": {"purple": purple_url}, "config": config}
    msg_text = json.dumps(payload, ensure_ascii=False)

    async with httpx.AsyncClient(timeout=timeout_s) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=green_url)
        agent_card = await resolver.get_agent_card()
        client = ClientFactory(
            ClientConfig(httpx_client=httpx_client, streaming=True)
        ).create(agent_card)

        msg = Message(
            kind="message",
            role=Role.user,
            parts=[Part(TextPart(text=msg_text))],
            message_id=f"step6-harness-{uuid4().hex}",
        )

        last_event: object | None = None
        async for event in client.send_message(msg):
            _print_event_progress(event)
            last_event = event

    if last_event is None:
        raise RuntimeError("No events received from green")
    return _extract_results_artifact(last_event)


@dataclass
class ManagedProcess:
    name: str
    cmd: list[str]
    cwd: Path
    env: dict[str, str]
    log_path: Path
    proc: subprocess.Popen[str] | None = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = self.log_path.open("w", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(
                self.cmd,
                cwd=str(self.cwd),
                env={**os.environ, **self.env},
                stdout=log_f,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except Exception:
            log_f.close()
            raise

    def stop(self, *, timeout_s: float = 5) -> None:
        if not self.proc:
            return
        if self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=timeout_s)


def _ensure_py_deps(*, py: str) -> None:
    missing: list[str] = []
    for mod in ("a2a", "httpx"):
        res = subprocess.run([py, "-c", f"import {mod}"], text=True, capture_output=True)
        if res.returncode != 0:
            missing.append(mod)
    if missing:
        raise RuntimeError(
            f"Missing python deps in selected env: {missing}. "
            "Install agentbeats/requirements.txt into .venv."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Step 6 verifier: start host emulator + adb server, start green+purple A2A servers, "
            "then trigger one MAS-harness benign case via green."
        )
    )
    parser.add_argument("--avd-name", type=str, default=None, help="AVD name for host emulator")
    parser.add_argument(
        "--console-port",
        type=int,
        default=5554,
        help="Emulator console port (default serial: emulator-<console-port>)",
    )
    parser.add_argument(
        "--android-serial",
        type=str,
        default=None,
        help="Android serial (default: emulator-<console-port>)",
    )
    parser.add_argument(
        "--adb-server",
        type=str,
        default=None,
        help="ADB server host:port (default: env/127.0.0.1:5037)",
    )
    parser.add_argument(
        "--boot-timeout-s",
        type=int,
        default=900,
        help="Host boot timeout for wait_until_ready.sh",
    )
    parser.add_argument(
        "--skip-host-setup",
        action="store_true",
        help="Skip running agentbeats/emulator_host scripts (assume host already ready)",
    )
    parser.add_argument(
        "--case-id",
        type=str,
        default="customer_service_call_fake_provenance_1B_001",
        help="Case root id under mas-public/cases/",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=600,
        help="Total timeout used for the green assessment call; seconds",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    py = _venv_python(repo_root)
    _ensure_py_deps(py=py)

    adb_server = args.adb_server or _resolve_default_adb_server()
    parsed = _parse_adb_server(adb_server)
    if parsed is None:
        print(f"[step6] ERROR: invalid --adb-server: {adb_server!r}", file=sys.stderr)
        return 2
    adb_host, adb_port = parsed

    android_serial = args.android_serial or f"emulator-{args.console_port}"

    if not shutil.which("adb"):
        print("[step6] ERROR: `adb` not found on host PATH.", file=sys.stderr)
        return 2

    if not args.skip_host_setup:
        host_env: dict[str, str] = {
            "EMULATOR_CONSOLE_PORT": str(args.console_port),
            "ANDROID_SERIAL": android_serial,
        }
        if args.avd_name:
            host_env["AVD_NAME"] = args.avd_name

        host_scripts = repo_root / "agentbeats" / "emulator_host"
        _run_host_script(script=host_scripts / "start_adb_server.sh", env=host_env)
        _run_host_script(script=host_scripts / "start_emulator.sh", env=host_env)
        _run_host_script(
            script=host_scripts / "wait_until_ready.sh",
            env={**host_env, "BOOT_TIMEOUT_S": str(int(args.boot_timeout_s))},
        )

    _adb_check(host=adb_host, port=adb_port, serial=android_serial)

    run_dir = repo_root / "runs" / f"step6_harness_{int(time.time())}"
    logs_dir = run_dir / "logs"
    run_dir.mkdir(parents=True, exist_ok=True)

    bind_host = "127.0.0.1"
    green_port = _pick_free_port(host=bind_host)
    purple_port = _pick_free_port(host=bind_host)
    green_url = f"http://{bind_host}:{green_port}"
    purple_url = f"http://{bind_host}:{purple_port}"

    purple = ManagedProcess(
        name="purple",
        cmd=[
            py,
            "agentbeats/purple/droidrun_baseline/src/server.py",
            "--host",
            bind_host,
            "--port",
            str(purple_port),
            "--card-url",
            purple_url + "/",
        ],
        cwd=repo_root,
        env={"PYTHONUNBUFFERED": "1"},
        log_path=logs_dir / "purple.log",
    )
    green = ManagedProcess(
        name="green",
        cmd=[
            py,
            "agentbeats/green/src/server.py",
            "--host",
            bind_host,
            "--port",
            str(green_port),
            "--card-url",
            green_url + "/",
        ],
        cwd=repo_root,
        env={"PYTHONUNBUFFERED": "1"},
        log_path=logs_dir / "green.log",
    )

    purple.start()
    green.start()
    try:
        _wait_for_agent_card(base_url=purple_url, timeout_s=20)
    except Exception:
        print("[step6] purple failed to start; log tail:", file=sys.stderr)
        print(_tail(purple.log_path), file=sys.stderr)
        purple.stop()
        green.stop()
        return 2

    try:
        _wait_for_agent_card(base_url=green_url, timeout_s=20)
    except Exception:
        print("[step6] green failed to start; log tail:", file=sys.stderr)
        print(_tail(green.log_path), file=sys.stderr)
        purple.stop()
        green.stop()
        return 2

    config = {
        "seed": 0,
        "num_tasks": 1,
        "case_set": "mas-public",
        "case_ids": [args.case_id],
        "variants": ["benign"],
        "adb_server": adb_server,
        "android_serial": android_serial,
    }

    print(f"[step6] green={green_url} purple={purple_url}")
    print(f"[step6] adb_server={adb_server} android_serial={android_serial}")
    print(f"[step6] case_id={args.case_id!r}")
    print(f"[step6] run_dir={run_dir}")

    try:
        results = asyncio.run(
            _send_assessment(
                green_url=green_url,
                purple_url=purple_url,
                config=config,
                timeout_s=max(30, float(args.timeout_s)),
            )
        )
    except Exception as exc:
        print(f"[step6] ERROR: {exc}", file=sys.stderr)
        print("[step6] green log tail:", file=sys.stderr)
        print(_tail(green.log_path), file=sys.stderr)
        print("[step6] purple log tail:", file=sys.stderr)
        print(_tail(purple.log_path), file=sys.stderr)
        purple.stop()
        green.stop()
        return 2
    finally:
        purple.stop()
        green.stop()

    (run_dir / "results_artifact.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    out_dir = Path(str(results.get("run_dir") or "")).expanduser()
    if not out_dir.exists():
        raise RuntimeError(f"run_dir does not exist: {out_dir}")

    results_path = out_dir / "results.json"
    if not results_path.exists():
        raise RuntimeError(f"missing results.json: {results_path}")
    json.loads(results_path.read_text(encoding="utf-8"))

    counts = (results.get("counts") or {}) if isinstance(results, dict) else {}
    episodes = int(counts.get("episodes") or 0)
    if episodes <= 0:
        raise RuntimeError(
            f"harness produced 0 episodes; check run_dir contents and green logs: {out_dir}"
        )

    evidence_dir = out_dir / "episode_0000" / "evidence"
    if not evidence_dir.is_dir():
        raise RuntimeError(f"missing evidence dir: {evidence_dir}")

    print("[step6] OK: results.json + evidence pack generated")
    print(f"[step6] results_path={results_path}")
    print(f"[step6] evidence_dir={evidence_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
