"""SQLite pull-to-host query oracles.

Phase 2 Step 13 (Oracle Zoo v1, F 类): SQLite Oracles.

Implements `SqlitePullQueryOracle` (v1 / recommended):
- pull a sqlite db file from the device to the host evidence bundle
- query it using Python's `sqlite3` (read-only usage)
- record structured rows + stable digest, with conservative capability gating
"""

from __future__ import annotations

import base64
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from mas_harness.oracles.zoo.base import (
    Oracle,
    OracleContext,
    OracleEvidence,
    make_decision,
    make_oracle_event,
    make_query,
    now_ms,
)
from mas_harness.oracles.zoo.registry import register_oracle
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256
from mas_harness.oracles.zoo.utils.time_window import TimeWindow, parse_epoch_time_ms

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(text: str, *, default: str = "db.sqlite") -> str:
    name = str(text or "").strip()
    name = name.rsplit("/", 1)[-1] if "/" in name else name
    name = _SAFE_NAME_RE.sub("_", name)
    return name or default


def _jsonify_sql_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes_b64__": base64.b64encode(bytes(value)).decode("ascii")}
    return str(value)


def _parse_ts_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return parse_epoch_time_ms(str(int(value)))
    return parse_epoch_time_ms(str(value))


def _pull_meta_to_dict(res: Any) -> Dict[str, Any]:
    if hasattr(res, "stdout") or hasattr(res, "returncode"):
        return {
            "args": getattr(res, "args", None),
            "returncode": getattr(res, "returncode", None),
            "stderr": getattr(res, "stderr", None),
            "stdout": str(getattr(res, "stdout", "") or ""),
        }
    return {"args": None, "returncode": 0, "stderr": None, "stdout": str(res)}


def _pull_error_kind(meta: Mapping[str, Any]) -> str | None:
    rc = meta.get("returncode")
    if isinstance(rc, int) and rc == 0:
        return None
    combined = (
        str(meta.get("stdout", "") or "") + "\n" + str(meta.get("stderr", "") or "")
    ).lower()
    if (
        "remote object does not exist" in combined
        or "no such file" in combined
        or "not found" in combined
    ):
        return "missing_file"
    if "permission denied" in combined or "securityexception" in combined:
        return "permission_denied"
    return "unknown_error"


def _window_meta(window: TimeWindow) -> Dict[str, Any]:
    return {
        "start_ms": window.start_ms,
        "end_ms": window.end_ms,
        "t0_ms": window.t0_ms,
        "slack_ms": window.slack_ms,
    }


def _row_matches_expected(row: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    for key, exp in expected.items():
        if row.get(str(key)) != exp:
            return False
    return True


class SqlitePullQueryOracle(Oracle):
    """Pull an on-device sqlite db to host and run a query."""

    oracle_id = "sqlite_pull_query"
    oracle_name = "sqlite_pull_query"
    oracle_type = "hard"
    capabilities_required = ("pull_file",)

    def __init__(
        self,
        *,
        remote_path: str,
        sql: str,
        expected: Optional[Mapping[str, Any]] = None,
        min_rows: int = 1,
        max_rows: int = 200,
        timeout_ms: int = 15_000,
        timestamp_column: Optional[str] = None,
    ) -> None:
        if not isinstance(remote_path, str) or not remote_path.strip():
            raise ValueError("SqlitePullQueryOracle requires non-empty remote_path string")
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("SqlitePullQueryOracle requires non-empty sql string")
        self._remote_path = str(remote_path)
        self._sql = str(sql)
        self._expected = dict(expected) if expected else {}
        self._min_rows = int(min_rows)
        self._max_rows = int(max_rows)
        self._timeout_ms = int(timeout_ms)
        self._timestamp_column = str(timestamp_column) if timestamp_column else None

        if self._min_rows < 0:
            raise ValueError("min_rows must be >= 0")
        if self._max_rows <= 0:
            raise ValueError("max_rows must be > 0")
        if self._expected and self._min_rows <= 0:
            # When using explicit matching, a min_rows of 0 is surprising.
            self._min_rows = 1

    def _missing_cap_event(
        self, *, phase: str, missing: Sequence[str], reason: str
    ) -> Dict[str, Any]:
        return make_oracle_event(
            ts_ms=now_ms(),
            oracle_id=self.oracle_id,
            oracle_name=self.oracle_name,
            oracle_type=self.oracle_type,
            phase=phase,
            queries=[
                make_query(
                    query_type="file_pull",
                    path=self._remote_path,
                    timeout_ms=self._timeout_ms,
                    serial=None,
                ),
                make_query(
                    query_type="sqlite",
                    path=self._remote_path,
                    sql=self._sql,
                    timeout_ms=0,
                    serial=None,
                ),
            ],
            result_for_digest={"missing": list(missing), "reason": reason},
            anti_gaming_notes=[
                (
                    "Hard oracle: pulls a sqlite db and queries it on the host; robust to UI "
                    "spoofing when tasks validate database state directly."
                ),
                (
                    "Anti-gaming: should be paired with an episode time window column or a "
                    "unique per-episode token to prevent stale/historical false positives."
                ),
            ],
            decision=make_decision(success=False, score=0.0, reason=reason, conclusive=False),
            capabilities_required=list(self.capabilities_required),
            missing_capabilities=list(missing),
        )

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller

        if not hasattr(controller, "pull_file"):
            return [
                self._missing_cap_event(
                    phase="post",
                    missing=["pull_file"],
                    reason="missing controller capability: pull_file",
                )
            ]

        rel_name = _safe_name(self._remote_path, default="db.sqlite")
        artifact_rel = Path("oracle_artifacts") / self.oracle_name / rel_name

        tmp_local: Optional[Path] = None
        local_path: Path
        if ctx.episode_dir is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
            tmp.close()
            tmp_local = Path(tmp.name)
            local_path = tmp_local
        else:
            local_path = ctx.episode_dir / artifact_rel
            local_path.parent.mkdir(parents=True, exist_ok=True)

        pull_exc: Optional[str] = None
        res: Any = None
        try:
            try:
                res = controller.pull_file(
                    self._remote_path,
                    local_path,
                    timeout_s=float(self._timeout_ms) / 1000.0,
                    check=False,
                )
            except TypeError:
                res = controller.pull_file(self._remote_path, local_path, check=False)
        except Exception as e:  # pragma: no cover
            pull_exc = repr(e)

        pull_meta = _pull_meta_to_dict(res) if res is not None else {"returncode": None}
        if pull_exc:
            pull_meta["exception"] = pull_exc

        pull_kind = _pull_error_kind(pull_meta)
        pull_ok = pull_kind is None and local_path.exists()

        queries: List[Dict[str, Any]] = [
            make_query(
                query_type="file_pull",
                path=self._remote_path,
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
                dst_rel=artifact_rel.as_posix() if ctx.episode_dir is not None else None,
            ),
            make_query(
                query_type="sqlite",
                path=self._remote_path,
                sql=self._sql,
                timeout_ms=0,
                serial=None,
            ),
        ]

        if not pull_ok:
            if tmp_local is not None:
                try:
                    tmp_local.unlink()
                except Exception:
                    pass

            conclusive = pull_kind == "missing_file"
            reason = (
                "missing sqlite db file on device" if conclusive else "failed to pull sqlite db"
            )
            result = {
                "remote_path": self._remote_path,
                "pull_ok": False,
                "pull_error_kind": pull_kind,
                "pull": pull_meta,
            }
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=queries,
                    result_for_digest=result,
                    result_preview={"remote_path": self._remote_path, "pull_error_kind": pull_kind},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: requires pulling the db file; pull failures are "
                            "treated as inconclusive unless the db is clearly missing."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason=reason,
                        conclusive=conclusive,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        db_sha256 = stable_file_sha256(local_path)
        artifacts = None
        if ctx.episode_dir is not None:
            try:
                artifacts = [
                    {
                        "path": artifact_rel.as_posix(),
                        "sha256": db_sha256,
                        "bytes": int(local_path.stat().st_size),
                        "mime": "application/x-sqlite3",
                    }
                ]
            except Exception:  # pragma: no cover
                artifacts = None

        columns: List[str] = []
        rows: List[Dict[str, Any]] = []
        query_error: Optional[str] = None
        truncated = False

        try:
            conn = sqlite3.connect(f"file:{local_path}?mode=ro", uri=True)
            try:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(self._sql)
                if cur.description:
                    columns = [str(d[0]) for d in cur.description]
                raw = cur.fetchmany(self._max_rows + 1)
                if len(raw) > self._max_rows:
                    truncated = True
                    raw = raw[: self._max_rows]
                for r in raw:
                    if isinstance(r, sqlite3.Row):
                        row_dict = {k: _jsonify_sql_value(r[k]) for k in columns}
                    else:
                        # Fallback: treat as tuple aligned with columns.
                        row_dict = {
                            (columns[i] if i < len(columns) else str(i)): _jsonify_sql_value(v)
                            for i, v in enumerate(r)
                        }
                    rows.append(row_dict)
            finally:
                conn.close()
        except Exception as e:
            query_error = f"{type(e).__name__}: {e}"

        if tmp_local is not None:
            try:
                tmp_local.unlink()
            except Exception:
                pass

        if query_error is not None:
            result = {
                "remote_path": self._remote_path,
                "db_sha256": db_sha256,
                "query_error": query_error,
                "sql": self._sql,
                "pull": pull_meta,
                "artifact_rel": artifact_rel.as_posix(),
            }
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=queries,
                    result_for_digest=result,
                    result_preview={"remote_path": self._remote_path, "query_error": query_error},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: sqlite query failures are treated as inconclusive "
                            "(cannot validate expected DB state)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="sqlite query failed",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    artifacts=artifacts,
                )
            ]

        candidate_rows = rows
        window: Optional[TimeWindow] = None
        window_meta: Dict[str, Any] | None = None
        timestamp_stats: Dict[str, Any] | None = None

        if self._timestamp_column:
            if ctx.episode_time is None:
                return [
                    self._missing_cap_event(
                        phase="post",
                        missing=["episode_time_anchor"],
                        reason="missing episode time anchor (time window unavailable)",
                    )
                ]
            window, window_meta = ctx.episode_time.device_window(controller=controller)
            if window is None:
                return [
                    self._missing_cap_event(
                        phase="post",
                        missing=["device_time_window"],
                        reason="failed to compute device time window",
                    )
                ]

            parsed = 0
            in_window = 0
            filtered: List[Dict[str, Any]] = []
            for row in rows:
                ts_val = row.get(self._timestamp_column)
                ts_ms = _parse_ts_ms(ts_val)
                if ts_ms is not None:
                    parsed += 1
                    if window.contains(ts_ms):
                        in_window += 1
                        filtered.append(row)
            candidate_rows = filtered
            timestamp_stats = {
                "timestamp_column": self._timestamp_column,
                "parsed_count": parsed,
                "in_window_count": in_window,
                "total_rows": len(rows),
            }

        matches: List[Dict[str, Any]] = []
        if self._expected:
            for row in candidate_rows:
                if _row_matches_expected(row, self._expected):
                    matches.append(row)
        else:
            matches = candidate_rows

        success = len(matches) >= self._min_rows
        reason: str
        if self._expected:
            reason = (
                f"matched {len(matches)} row(s) with expected fields"
                if success
                else "no matching rows"
            )
        else:
            reason = f"returned {len(candidate_rows)} row(s)" if success else "no rows returned"

        result_for_digest = {
            "remote_path": self._remote_path,
            "db_sha256": db_sha256,
            "sql": self._sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "max_rows": self._max_rows,
            "expected": self._expected,
            "min_rows": self._min_rows,
            "timestamp_column": self._timestamp_column,
            "timestamp_stats": timestamp_stats,
            "device_window": _window_meta(window) if window else None,
            "device_window_meta": window_meta,
            "pull": pull_meta,
            "artifact_rel": artifact_rel.as_posix(),
        }

        preview = {
            "success": success,
            "row_count": len(rows),
            "candidate_row_count": len(candidate_rows),
            "match_count": len(matches),
            "expected": self._expected,
            "truncated": truncated,
            "sample_rows": matches[:3] if matches else rows[:3],
            "artifact_rel": artifact_rel.as_posix() if ctx.episode_dir is not None else None,
        }

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=queries,
                result_for_digest=result_for_digest,
                result_preview=preview,
                anti_gaming_notes=[
                    "Hard oracle: validates task success by querying app/database state directly.",
                    (
                        "Time window (optional): set timestamp_column to restrict matches to the "
                        "episode device-time window (prevents stale/historical false positives)."
                    ),
                    "Digest: records a stable sha256 of the pulled db file plus query result rows.",
                ],
                decision=make_decision(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason=reason,
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
                artifacts=artifacts,
            )
        ]


@register_oracle(SqlitePullQueryOracle.oracle_id)
def _make_sqlite_pull_query(cfg: Mapping[str, Any]) -> Oracle:
    remote_path = cfg.get("remote_path") or cfg.get("path")
    sql = cfg.get("sql") or cfg.get("query")
    expected = cfg.get("expected") or cfg.get("expect") or {}
    timestamp_column = cfg.get("timestamp_column") or cfg.get("ts_column")

    if not isinstance(remote_path, str) or not remote_path:
        raise ValueError("SqlitePullQueryOracle requires 'remote_path' string")
    if not isinstance(sql, str) or not sql:
        raise ValueError("SqlitePullQueryOracle requires 'sql' string")
    if expected is None:
        expected = {}
    if not isinstance(expected, Mapping):
        raise ValueError("expected/expect must be an object")

    return SqlitePullQueryOracle(
        remote_path=remote_path,
        sql=sql,
        expected=dict(expected),
        min_rows=int(cfg.get("min_rows", 1)),
        max_rows=int(cfg.get("max_rows", 200)),
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
        timestamp_column=str(timestamp_column) if timestamp_column else None,
    )


@register_oracle("SqlitePullQueryOracle")
def _make_sqlite_pull_query_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_sqlite_pull_query(cfg)
