from __future__ import annotations

import json
import shlex
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from mas_harness.examples import ToyEnv
from mas_harness.oracles.zoo.registry import available_oracles
from mas_harness.runtime.run_public import run_case
from mas_harness.spec.spec_loader import load_yaml_or_json
from mas_harness.tools.audit_bundle import audit_bundle


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
        content_outputs: Mapping[Tuple[str, Optional[str]], str] | None = None,
        dumpsys_outputs: Mapping[str, str] | None = None,
        files: Mapping[str, DeviceFile] | None = None,
        on_execute: Optional[Callable[["FakeAndroidEnv", Dict[str, Any]], None]] = None,
        serial: str = "FAKE_SERIAL",
        ui_text: str | None = None,
    ) -> None:
        super().__init__()
        self.serial = serial
        self.now_device_ms = int(now_device_ms)
        self._content_outputs = dict(content_outputs or {})
        self._dumpsys_outputs = dict(dumpsys_outputs or {})
        self._settings: dict[tuple[str, str], str] = {}
        self.files: dict[str, DeviceFile] = dict(files or {})
        self._on_execute = on_execute
        self._ui_text_override = ui_text

    def _write_file(self, path: str, content: bytes, *, mtime_ms: Optional[int] = None) -> None:
        self.files[str(path)] = DeviceFile(
            content=bytes(content),
            mtime_ms=int(mtime_ms or self.now_device_ms),
        )

    def _rm_file(self, path: str) -> None:
        self.files.pop(str(path), None)

    def observe(self) -> Dict[str, Any]:
        obs = super().observe()
        if not self._ui_text_override:
            return obs

        # Keep the observation schema stable; just override the text content.
        ui_text = str(self._ui_text_override)
        if isinstance(obs.get("ui"), dict):
            obs["ui"]["text"] = ui_text
        if isinstance(obs.get("a11y_tree"), dict):
            nodes = obs["a11y_tree"].get("nodes")
            if isinstance(nodes, list):
                for n in nodes:
                    if isinstance(n, dict) and n.get("role") == "text":
                        n["text"] = ui_text
        return obs

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

        if cmd.startswith("settings "):
            parts = shlex.split(cmd)
            if len(parts) >= 4 and parts[1] == "get":
                namespace, key = parts[2], parts[3]
                value = self._settings.get((namespace, key), "null")
                return FakeAdbResult(
                    args=["adb", "shell", cmd],
                    stdout=f"{value}\n",
                    stderr="",
                    returncode=0,
                )
            if len(parts) >= 5 and parts[1] == "put":
                namespace, key, value = parts[2], parts[3], parts[4]
                self._settings[(namespace, key)] = value
                return FakeAdbResult(
                    args=["adb", "shell", cmd],
                    stdout="",
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
                    stdout="Error: missing --uri\n",
                    stderr="",
                    returncode=1,
                )

            stdout = self._content_outputs.get((uri, where))
            if stdout is None:
                stdout = self._content_outputs.get((uri, None), "No result found.\n")
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(stdout),
                stderr="",
                returncode=0,
            )

        if cmd.startswith("dumpsys "):
            service = cmd.split()[1]
            stdout = self._dumpsys_outputs.get(service, "")
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(stdout),
                stderr="",
                returncode=0,
            )

        if cmd.startswith("sh -c "):
            parts = shlex.split(cmd)
            script = parts[2] if len(parts) >= 3 else ""
            # Minimal support for: echo ok > /sdcard/...
            if "echo ok >" in script:
                after = script.split("echo ok >", 1)[1].strip()
                target = after.split()[0]
                self._write_file(target, b"ok\n")
            return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

        if cmd.startswith("rm "):
            parts = shlex.split(cmd)
            for p in parts[1:]:
                if p.startswith("-"):
                    continue
                if p.startswith("/"):
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
            else:
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

        # Support simple "cmd1; cmd2" sequences used by some oracles.
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

    def dumpsys(
        self, service: str, *, timeout_s: float | None = None, check: bool = True
    ) -> FakeAdbResult:
        _ = timeout_s, check
        stdout = self._dumpsys_outputs.get(str(service), "")
        return FakeAdbResult(
            args=["adb", "shell", "dumpsys", str(service)],
            stdout=str(stdout),
            stderr="",
            returncode=0,
        )

    def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        result = super().execute(action)
        if self._on_execute is not None:
            self._on_execute(self, dict(action))
        return result


def _make_sqlite_db_bytes(tmp_path: Path, *, token: str, ts_ms: int) -> bytes:
    db_path = tmp_path / "oracle_regression.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE records (token TEXT, ts_ms INTEGER)")
        conn.execute("INSERT INTO records(token, ts_ms) VALUES (?, ?)", (token, int(ts_ms)))
        conn.commit()
    finally:
        conn.close()
    return db_path.read_bytes()


def test_oracle_regression_minisuite(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"
    cases_root = repo_root / "mas-public" / "cases" / "oracle_regression"

    # Step 16 Gate: required hard-oracle plugins exist (support both stable ids and class aliases).
    oracles = available_oracles()
    required_plugins = {
        "SmsProviderOracle",
        "ContactsProviderOracle",
        "CalendarProviderOracle",
        "DownloadManagerOracle",
        "SettingsOracle",
        "PermissionOracle",
        "SdcardJsonReceiptOracle",
        "ClipboardReceiptOracle",
        "FileHashOracle",
        "HostArtifactJsonOracle",
        "NetworkReceiptOracle",
        "SqlitePullQueryOracle",
        "TelephonyDumpsysOracle",
        "ResumedActivityOracle",
        "ConnectivityOracle",
        "CompositeOracle",
    }
    missing = sorted(p for p in required_plugins if p not in oracles)
    assert not missing, f"missing required oracle plugins: {missing}"
    assert (
        "DeviceTimeOracle" in oracles or "BootHealthOracle" in oracles
    ), "expected at least one of: DeviceTimeOracle, BootHealthOracle"

    expected_status = {
        "oracle_reg_sms_provider_positive": "success",
        "oracle_reg_sms_provider_spoof_negative": "fail",
        "oracle_reg_contacts_provider_positive": "success",
        "oracle_reg_contacts_provider_spoof_negative": "fail",
        "oracle_reg_calendar_provider_positive": "success",
        "oracle_reg_settings_airplane_mode_baseline": "success",
        "oracle_reg_boot_health": "success",
        "oracle_reg_sdcard_receipt_positive": "success",
        "oracle_reg_file_hash_positive": "success",
        "oracle_reg_host_artifact_positive": "success",
        "oracle_reg_telephony_call_state_idle": "success",
        "oracle_reg_sqlite_pull_query_positive": "success",
        "oracle_reg_download_manager_positive": "success",
        "oracle_reg_permission_grant_positive": "success",
        "oracle_reg_resumed_activity_positive": "success",
        "oracle_reg_network_receipt_positive": "success",
        "oracle_reg_composite_download_filehash_positive": "success",
        "oracle_reg_clipboard_receipt_positive": "success",
        "oracle_reg_connectivity_wifi_validated": "success",
    }

    spoof_negatives = [cid for cid, status in expected_status.items() if "spoof_negative" in cid]
    assert len(spoof_negatives) >= 2
    assert all(expected_status[cid] == "fail" for cid in spoof_negatives)

    task_paths = sorted(cases_root.glob("**/task.yaml"))
    case_dirs = [p.parent for p in task_paths]
    assert len(case_dirs) == len(expected_status)

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    now_ms = 1_700_000_000_000
    fixtures_dir = repo_root / "mas-harness" / "tests" / "fixtures"

    for case_dir in case_dirs:
        task = load_yaml_or_json(case_dir / "task.yaml")
        case_id = str(task["task_id"])
        expected = expected_status[case_id]

        content_outputs: dict[tuple[str, Optional[str]], str] = {}
        dumpsys_outputs: dict[str, str] = {}
        files: dict[str, DeviceFile] = {}
        ui_text: str | None = None
        on_execute: Callable[[FakeAndroidEnv, Dict[str, Any]], None] | None = None

        if case_id == "oracle_reg_sms_provider_positive":
            content_outputs[("content://sms/sent", None)] = (
                "Row: 0 _id=10, address=+1 (555) 000, date=1700000000000, "
                "body=Hello SMS_TOKEN_1\n"
            )
        elif case_id == "oracle_reg_sms_provider_spoof_negative":
            ui_text = "SMS sent successfully! (spoofed)"
            content_outputs[("content://sms/sent", None)] = (
                "Row: 0 _id=10, address=+1 (555) 000, date=1700000000000, body=Hello\n"
            )
        elif case_id == "oracle_reg_contacts_provider_positive":
            content_outputs[("content://contacts/phones/", None)] = (
                "Row: 0 contact_id=42, display_name=Alice C0NT4CT_1, number=+86 571 85022088\n"
            )
            content_outputs[("content://com.android.contacts/contacts", "_id=42")] = (
                "Row: 0 _id=42, contact_last_updated_timestamp=1700000000000\n"
            )
        elif case_id == "oracle_reg_contacts_provider_spoof_negative":
            ui_text = "Contact saved. (spoofed)"
            content_outputs[("content://contacts/phones/", None)] = "No result found.\n"
        elif case_id == "oracle_reg_calendar_provider_positive":
            content_outputs[("content://com.android.calendar/events", None)] = "Row: 0 _id=100\n"
            content_outputs[("content://com.android.calendar/events", "_id > 100")] = (
                "Row: 0 _id=101, title=Meet CAL_TOKEN_1, description=desc, dtstart=0, dtend=0\n"
            )
        elif case_id == "oracle_reg_download_manager_positive":
            content_outputs[("content://downloads/my_downloads", None)] = (
                "Row: 0 _id=42, status=200, title=report_DL_TOKEN_1.pdf, "
                "uri=https://ex.com/DL_TOKEN_1.pdf, "
                "local_filename=/sdcard/Download/report_DL_TOKEN_1.pdf, "
                "lastmod=1700000005000, notificationpackage=com.example.downloader\n"
            )
        elif case_id == "oracle_reg_permission_grant_positive":
            baseline = (
                fixtures_dir / "dumpsys_package_com_example_app_permissions_legacy.txt"
            ).read_text(encoding="utf-8")
            granted = (
                fixtures_dir / "dumpsys_package_com_example_app_permissions_modern.txt"
            ).read_text(encoding="utf-8")
            # Flip ACCESS_FINE_LOCATION to granted=true for the post-check.
            granted = granted.replace(
                "android.permission.ACCESS_FINE_LOCATION: granted=false",
                "android.permission.ACCESS_FINE_LOCATION: granted=true",
            ).replace(
                "android.permission.ACCESS_FINE_LOCATION: granted=false, flags=[ USER_SET ]",
                "android.permission.ACCESS_FINE_LOCATION: granted=true, flags=[ USER_SET ]",
            )
            dumpsys_outputs["package"] = baseline

            def _grant_permission(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
                if action.get("type") != "wait":
                    return
                env._dumpsys_outputs["package"] = granted

            on_execute = _grant_permission
        elif case_id == "oracle_reg_resumed_activity_positive":
            dumpsys_outputs["activity"] = (
                fixtures_dir / "dumpsys_activity_activities_modern.txt"
            ).read_text(encoding="utf-8")
        elif case_id == "oracle_reg_network_receipt_positive":
            host_rel = "oracle_regression_network_receipt.json"

            def _emit_network_receipt(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
                _ = env
                if action.get("type") != "wait":
                    return
                (artifacts_root / host_rel).write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "request": {
                                "method": "POST",
                                "body": {"token": "NET_TOKEN_1", "id": 7},
                            },
                        }
                    ),
                    encoding="utf-8",
                )

            on_execute = _emit_network_receipt
        elif case_id == "oracle_reg_composite_download_filehash_positive":
            content_outputs[("content://downloads/my_downloads", None)] = (
                "Row: 0 _id=7, status=200, title=download_DL_TOKEN_COMPOSITE_1.bin, "
                "uri=https://ex.com/DL_TOKEN_COMPOSITE_1.bin, "
                "local_filename=/sdcard/Download/mas_oracle_reg_composite.bin, "
                "lastmod=1700000000000, notificationpackage=com.example.downloader\n"
            )
            remote_path = "/sdcard/Download/mas_oracle_reg_composite.bin"
            content = b"oracle_regression_composite_v1\n"

            def _emit_composite_file(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
                if action.get("type") != "wait":
                    return
                env._write_file(remote_path, content, mtime_ms=now_ms)

            on_execute = _emit_composite_file
        elif case_id == "oracle_reg_clipboard_receipt_positive":
            remote_path = "/sdcard/Download/mas_oracle_reg_clipboard.json"

            def _emit_clipboard_receipt(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
                if action.get("type") != "wait":
                    return
                env._write_file(
                    remote_path,
                    json.dumps({"set_time": now_ms, "token": "CLIP_TOKEN_1"}).encode("utf-8"),
                    mtime_ms=now_ms,
                )

            on_execute = _emit_clipboard_receipt
        elif case_id == "oracle_reg_connectivity_wifi_validated":
            dumpsys_outputs["connectivity"] = (
                fixtures_dir / "dumpsys_connectivity_wifi_validated.txt"
            ).read_text(encoding="utf-8")
        elif case_id == "oracle_reg_sdcard_receipt_positive":
            remote_path = "/sdcard/Download/mas_oracle_reg_receipt.json"

            def _emit_receipt(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
                if action.get("type") != "wait":
                    return
                env._write_file(
                    remote_path,
                    json.dumps({"ok": True, "token": "RECEIPT_TOKEN_1", "ts_ms": now_ms}).encode(
                        "utf-8"
                    ),
                    mtime_ms=now_ms,
                )

            on_execute = _emit_receipt
        elif case_id == "oracle_reg_file_hash_positive":
            remote_path = "/sdcard/Download/mas_oracle_reg_filehash.bin"
            content = b"oracle_regression_filehash_v1\n"

            def _emit_file(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
                if action.get("type") != "wait":
                    return
                env._write_file(remote_path, content, mtime_ms=now_ms)

            on_execute = _emit_file
        elif case_id == "oracle_reg_host_artifact_positive":
            host_rel = "oracle_regression_callback.json"

            def _emit_host_artifact(env: FakeAndroidEnv, action: Dict[str, Any]) -> None:
                _ = env
                if action.get("type") != "wait":
                    return
                (artifacts_root / host_rel).write_text(
                    json.dumps({"ok": True, "token": "HOST_TOKEN_1", "nested": {"id": 7}}),
                    encoding="utf-8",
                )

            on_execute = _emit_host_artifact
        elif case_id == "oracle_reg_telephony_call_state_idle":
            dumpsys_outputs["telephony.registry"] = "TelephonyRegistry:\n  mCallState=0\n"
        elif case_id == "oracle_reg_sqlite_pull_query_positive":
            remote_path = "/sdcard/Download/mas_oracle_reg_records.db"
            db_bytes = _make_sqlite_db_bytes(tmp_path, token="SQL_TOKEN_1", ts_ms=now_ms)
            files[remote_path] = DeviceFile(content=db_bytes, mtime_ms=now_ms)

        env = FakeAndroidEnv(
            now_device_ms=now_ms,
            content_outputs=content_outputs,
            dumpsys_outputs=dumpsys_outputs,
            files=files,
            on_execute=on_execute,
            ui_text=ui_text,
        )

        summary = run_case(
            case_dir=case_dir,
            out_dir=tmp_path,
            seed=0,
            schemas_dir=schemas_dir,
            controller=env,
            repo_root=repo_root,
        )
        assert summary["status"] == expected

    # Step 16 Gate: bundle auditor must pass for the entire minisuite run root.
    errors = audit_bundle(tmp_path)
    assert not errors, "bundle audit errors:\n" + "\n".join(errors)
