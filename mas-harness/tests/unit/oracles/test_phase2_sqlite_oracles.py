from __future__ import annotations

import json
import shlex
import sqlite3
import tempfile
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

    def _write_file(self, path: str, content: bytes, *, mtime_ms: Optional[int] = None) -> None:
        self.files[str(path)] = DeviceFile(
            content=bytes(content),
            mtime_ms=int(mtime_ms or self.now_device_ms),
        )

    def _rm_file(self, path: str) -> None:
        self.files.pop(str(path), None)

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
                args=["adb", "shell", cmd], stdout=str(self.now_device_ms), stderr="", returncode=0
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
            return FakeAdbResult(args=["adb", "shell", cmd], stdout="1\n", stderr="", returncode=0)

        if cmd.startswith("sh -c "):
            # Minimal support for BootHealth probe: echo ok > /sdcard/...
            if "echo ok >" in cmd:
                target = cmd.split("echo ok >", 1)[1].strip().strip("'").strip('"')
                self._write_file(target, b"ok\n")
            return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

        if cmd.startswith("rm "):
            for token in cmd.split():
                if token.startswith("/"):
                    self._rm_file(token)
            return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)

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

    def root_shell(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        check: bool = True,
    ) -> FakeAdbResult:
        _ = timeout_s, check
        cmd = str(command).strip()

        # Parse the `sqlite3 -json <db> <sql>` pattern used by RootSqliteOracle.
        tokens = shlex.split(cmd)
        if len(tokens) >= 4 and tokens[0] == "sqlite3" and tokens[1] == "-json":
            db_path = tokens[2]
            sql = tokens[3]
            db_file = self.files.get(db_path)
            if db_file is None:
                return FakeAdbResult(
                    args=["su", "0", "sqlite3"], stdout="", stderr="missing db\n", returncode=1
                )

            tmp_path = None
            try:
                tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
                tmp_path = Path(tmp.name)
                tmp.close()
                tmp_path.write_bytes(db_file.content)
                conn = sqlite3.connect(str(tmp_path))
                conn.row_factory = sqlite3.Row
                try:
                    cur = conn.execute(sql)
                    rows = [
                        {str(k): r[k] for k in r.keys()}
                        for r in cur.fetchall()
                        if isinstance(r, sqlite3.Row)
                    ]
                finally:
                    conn.close()
                return FakeAdbResult(
                    args=["su", "0", "sqlite3", "-json", db_path],
                    stdout=json.dumps(rows),
                    stderr="",
                    returncode=0,
                )
            except Exception as e:  # pragma: no cover
                return FakeAdbResult(
                    args=["su", "0", "sqlite3"], stdout="", stderr=repr(e), returncode=1
                )
            finally:
                if tmp_path is not None:
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass

        return FakeAdbResult(args=["su", "0", "sh", "-c", cmd], stdout="", stderr="", returncode=0)

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


def _make_sqlite_db_bytes(tmp_path: Path, *, token: str, ts_ms: int) -> bytes:
    db_path = tmp_path / "source.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE records (token TEXT, ts_ms INTEGER)")
        conn.execute("INSERT INTO records(token, ts_ms) VALUES (?, ?)", (token, int(ts_ms)))
        conn.commit()
    finally:
        conn.close()
    return db_path.read_bytes()


def test_sqlite_pull_query_oracle_task_success(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    token = "SQLITE_TOKEN_1"
    now_ms = 1_700_000_010_000
    remote_path = "/sdcard/app_records.db"
    db_bytes = _make_sqlite_db_bytes(tmp_path, token=token, ts_ms=now_ms)

    env = FakeAndroidEnv(
        now_device_ms=now_ms,
        files={remote_path: DeviceFile(content=db_bytes, mtime_ms=now_ms)},
    )

    case_dir = tmp_path / "case_sqlite_pull_query_success"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: sqlite_pull_query_success
            user_goal: "[TEST] SqlitePullQueryOracle success"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 0
            success_oracle:
              type: sqlite_pull_query
              remote_path: {remote_path}
              sql: "SELECT token, ts_ms FROM records WHERE token = '{token}';"
              expected:
                token: "{token}"
              timestamp_column: ts_ms
              min_rows: 1
              max_rows: 50
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


def test_root_sqlite_oracle_task_success(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    token = "SQLITE_TOKEN_2"
    now_ms = 1_700_000_011_000
    remote_path = "/sdcard/app_records_root.db"
    db_bytes = _make_sqlite_db_bytes(tmp_path, token=token, ts_ms=now_ms)

    env = FakeAndroidEnv(
        now_device_ms=now_ms,
        files={remote_path: DeviceFile(content=db_bytes, mtime_ms=now_ms)},
    )

    case_dir = tmp_path / "case_root_sqlite_success"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: root_sqlite_success
            user_goal: "[TEST] RootSqliteOracle success"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 0
            success_oracle:
              type: root_sqlite
              db_path: {remote_path}
              sql: "SELECT token, ts_ms FROM records WHERE token = '{token}';"
              expected:
                token: "{token}"
              timestamp_column: ts_ms
              min_rows: 1
              max_rows: 50
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


def test_sqlite_pull_query_missing_pull_file_is_inconclusive(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    case_dir = tmp_path / "case_sqlite_pull_missing_pull_file"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            """\
            task_id: sqlite_pull_missing_pull_file
            user_goal: "[TEST] Inconclusive when missing pull_file"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            success_oracle:
              type: sqlite_pull_query
              remote_path: /sdcard/does_not_matter.db
              sql: "SELECT 1;"
            """
        ),
    )

    # Use the default ToyEnv (no pull_file capability).
    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
    )
    assert summary["status"] == "inconclusive"
    assert summary["failure_class"] == "oracle_inconclusive"
