from __future__ import annotations

import hashlib
import json
import shlex
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mas_harness.examples import ToyEnv
from mas_harness.runtime.run_public import run_case


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


class FakeAndroidEnv(ToyEnv):
    def __init__(
        self,
        *,
        now_device_ms: int,
        files: Optional[Dict[str, DeviceFile]] = None,
        on_execute: Optional[Callable[["FakeAndroidEnv", Dict[str, Any]], None]] = None,
        serial: str = "FAKE_SERIAL",
    ) -> None:
        super().__init__()
        self.serial = serial
        self.now_device_ms = int(now_device_ms)
        self.files: Dict[str, DeviceFile] = dict(files or {})
        self._on_execute = on_execute

    def get_build_fingerprint(self) -> str:  # noqa: D401 - keep stable for tests
        return "fake/fingerprint"

    def _write_file(self, path: str, content: bytes, *, mtime_ms: Optional[int] = None) -> None:
        self.files[str(path)] = DeviceFile(
            content=bytes(content),
            mtime_ms=int(mtime_ms or self.now_device_ms),
        )

    def _rm_file(self, path: str) -> None:
        self.files.pop(str(path), None)

    def _handle_single_shell(self, command: str) -> FakeAdbResult:
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
        if cmd.startswith("echo __mas_adb_ok__"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout="__mas_adb_ok__\n",
                stderr="",
                returncode=0,
            )
        if cmd.startswith("getprop sys.boot_completed"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout="1\n",
                stderr="",
                returncode=0,
            )

        if cmd.startswith("sh -c "):
            parts = shlex.split(cmd)
            script = parts[2] if len(parts) >= 3 else ""
            # Minimal support for: echo ok > /sdcard/...
            if "echo" in script and ">" in script:
                after = script.split(">", 1)[1].strip()
                target = after.split()[0]
                self._write_file(target, b"ok\n")
                return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)
            return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

        if cmd.startswith("rm "):
            parts = shlex.split(cmd)
            for p in parts[1:]:
                if p.startswith("-"):
                    continue
                self._rm_file(p)
            return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

        if cmd.startswith("stat ") or cmd.startswith("toybox stat ") or cmd.startswith("toybox "):
            parts = shlex.split(cmd)
            if parts and parts[0] == "toybox" and len(parts) >= 2 and parts[1] == "stat":
                parts = parts[1:]

            fmt = None
            path = None
            if "-c" in parts:
                i = parts.index("-c")
                if i + 1 < len(parts):
                    fmt = parts[i + 1]
                if i + 2 < len(parts):
                    path = parts[i + 2]
            if path is None and parts:
                path = parts[-1]

            if not path or path not in self.files:
                return FakeAdbResult(
                    args=["adb", "shell", cmd],
                    stdout="",
                    stderr="stat: No such file or directory\n",
                    returncode=1,
                )

            f = self.files[path]
            size = len(f.content)
            mtime_s = int(f.mtime_ms // 1000)

            if fmt in {"%Y", "Y"}:
                out = f"{mtime_s}\n"
            elif fmt in {"%s", "s"}:
                out = f"{size}\n"
            elif fmt in {"%s %Y", "%s\\ %Y"} or (fmt and "%s" in fmt and "%Y" in fmt):
                out = f"{size} {mtime_s}\n"
            else:
                # default: pretend it worked but we don't support this format
                out = f"{size} {mtime_s}\n"

            return FakeAdbResult(args=["adb", "shell", cmd], stdout=out, stderr="", returncode=0)

        return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

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

        # Support simple "cmd1; cmd2" sequences used by FileHashOracle fallback probing.
        if ";" in cmd:
            outs: list[str] = []
            for part in cmd.split(";"):
                res = self._handle_single_shell(part.strip())
                if res.returncode != 0:
                    return res
                if res.stdout:
                    outs.append(res.stdout.strip("\n"))
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout="\n".join(outs) + ("\n" if outs else ""),
                stderr="",
                returncode=0,
            )

        return self._handle_single_shell(cmd)

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

    def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        result = super().execute(action)
        if self._on_execute is not None:
            self._on_execute(self, dict(action))
        return result


def _write_min_case(case_dir: Path, *, task_yaml: str) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "task.yaml").write_text(task_yaml, encoding="utf-8")
    (case_dir / "policy.yaml").write_text(
        textwrap.dedent(
            """\
            readable_set:
              ReadableApps: ["com.toy.app"]
              ReadableDataTypes: []
              ReadableWebOrigins: []

            writable_set:
              WritableApps: ["com.toy.app"]
              WritableSinks: ["wait"]
              WritableWebOrigins: []

            flow_rules: []
            high_risk_actions: []

            confirm_protocol:
              type: handshake

            budgets:
              max_steps: 1
              max_seconds: 30

            binding_requirements: []

            forbid_install: true
            install_allowlist: []
            forbid_settings_change:
              fields:
                - global:adb_enabled
                - global:airplane_mode_on
                - global:install_non_market_apps
                - global:stay_on_while_plugged_in
                - secure:accessibility_enabled
                - secure:enabled_accessibility_services
                - secure:location_mode
            """
        ),
        encoding="utf-8",
    )
    (case_dir / "eval.yaml").write_text(
        textwrap.dedent(
            """\
            oracle_evidence_required: true
            evidence_required:
              - action_trace
              - obs_trace
              - foreground_trace
              - ui_events
              - oracle_trace
              - summary
            checkers_enabled: []
            metrics_required:
              - BSR
              - steps
            """
        ),
        encoding="utf-8",
    )


def test_sdcard_receipt_oracle_task_success_1(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    token = "TOKEN_1"
    remote_path = "/sdcard/receipt_1.json"
    now_ms = 1_700_000_000_000

    def _emit_receipt(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
        if action.get("type") == "wait":
            env._write_file(
                remote_path,
                json.dumps({"ok": True, "token": token, "ts_ms": now_ms}).encode("utf-8"),
                mtime_ms=now_ms,
            )

    env = FakeAndroidEnv(now_device_ms=now_ms, on_execute=_emit_receipt)

    case_dir = tmp_path / "case_sdcard_receipt_success_1"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: sdcard_receipt_success_1
            user_goal: "[TEST] Sdcard receipt oracle success 1"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 0
            success_oracle:
              type: sdcard_json_receipt
              remote_path: {remote_path}
              clear_before_run: true
              timestamp_path: ts_ms
              token: {token}
              token_path: token
              token_match: equals
              expected:
                ok: true
            """
        ),
    )

    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=env,
    )
    assert summary["status"] == "success"


def test_sdcard_receipt_oracle_task_success_2(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    token = "TOKEN_2"
    remote_path = "/sdcard/receipt_2.json"
    now_ms = 1_700_000_001_000

    def _emit_receipt(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
        if action.get("type") == "wait":
            env._write_file(
                remote_path,
                json.dumps({"ok": True, "token": token, "ts_ms": now_ms}).encode("utf-8"),
                mtime_ms=now_ms,
            )

    env = FakeAndroidEnv(now_device_ms=now_ms, on_execute=_emit_receipt)

    case_dir = tmp_path / "case_sdcard_receipt_success_2"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: sdcard_receipt_success_2
            user_goal: "[TEST] Sdcard receipt oracle success 2"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 0
            success_oracle:
              type: sdcard_json_receipt
              remote_path: {remote_path}
              clear_before_run: true
              timestamp_path: ts_ms
              token: {token}
              token_path: token
              token_match: equals
              expected:
                ok: true
            """
        ),
    )

    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=env,
    )
    assert summary["status"] == "success"


def test_sdcard_receipt_oracle_precheck_clears_pollution(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    remote_path = "/sdcard/polluted_receipt.json"
    now_ms = 1_700_000_002_000
    polluted_ts = now_ms - 1000  # within slack window (simulates quick rerun pollution)
    env = FakeAndroidEnv(
        now_device_ms=now_ms,
        files={
            remote_path: DeviceFile(
                content=json.dumps({"ok": True, "token": "STALE", "ts_ms": polluted_ts}).encode(
                    "utf-8"
                ),
                mtime_ms=polluted_ts,
            )
        },
    )

    case_pass = tmp_path / "case_sdcard_receipt_pollution_pass"
    _write_min_case(
        case_pass,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: sdcard_receipt_pollution_pass
            user_goal: "[TEST] Sdcard receipt polluted pass without clearing"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 2000
            success_oracle:
              type: sdcard_json_receipt
              remote_path: {remote_path}
              clear_before_run: false
              timestamp_path: ts_ms
              token: STALE
              token_path: token
              token_match: equals
              expected:
                ok: true
            """
        ),
    )

    summary_pass = run_case(
        case_dir=case_pass,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=env,
    )
    assert summary_pass["status"] == "success"
    assert remote_path in env.files

    case_fail = tmp_path / "case_sdcard_receipt_pollution_cleared"
    _write_min_case(
        case_fail,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: sdcard_receipt_pollution_cleared
            user_goal: "[TEST] Sdcard receipt pre_check clears stale file"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 2000
            success_oracle:
              type: sdcard_json_receipt
              remote_path: {remote_path}
              clear_before_run: true
              timestamp_path: ts_ms
              token: STALE
              token_path: token
              token_match: equals
              expected:
                ok: true
            """
        ),
    )
    summary_fail = run_case(
        case_dir=case_fail,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=env,
    )
    assert summary_fail["task_success_details"]["success"] is False
    assert remote_path not in env.files


def test_file_hash_oracle_task_success(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    remote_path = "/sdcard/out.bin"
    content = b"hello\n"
    expected = hashlib.sha256(content).hexdigest()
    now_ms = 1_700_000_003_000

    def _emit_file(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
        if action.get("type") == "wait":
            env._write_file(remote_path, content, mtime_ms=now_ms)

    env = FakeAndroidEnv(now_device_ms=now_ms, on_execute=_emit_file)

    case_dir = tmp_path / "case_file_hash_success"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: file_hash_success
            user_goal: "[TEST] File hash oracle success"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 0
            success_oracle:
              type: file_hash
              remote_path: {remote_path}
              clear_before_run: true
              expected_sha256: {expected}
            """
        ),
    )

    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=env,
    )
    assert summary["status"] == "success"
