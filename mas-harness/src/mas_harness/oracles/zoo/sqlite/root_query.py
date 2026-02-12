"""SQLite root query oracles.

Phase 2 Step 13 (Oracle Zoo v1, F 类): SQLite Oracles.

Implements `RootSqliteOracle` (v2 / enhanced):
- run `su 0 sqlite3 ...` on the device (requires root)
- expect JSON output via `sqlite3 -json ...` and parse structured rows
- apply the same conservative capability gating (root unavailable => inconclusive)
"""

from __future__ import annotations

import json
import re
import shlex
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
from mas_harness.oracles.zoo.utils.time_window import TimeWindow, parse_epoch_time_ms


def _parse_ts_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return parse_epoch_time_ms(str(int(value)))
    return parse_epoch_time_ms(str(value))


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


_SQLITE_ERROR_RE = re.compile(r"(^|\\n)error:", re.IGNORECASE)


class RootSqliteOracle(Oracle):
    """Run a sqlite3 query on-device via `su 0`."""

    oracle_id = "root_sqlite"
    oracle_name = "root_sqlite"
    oracle_type = "hard"
    capabilities_required = ("root_shell",)

    def __init__(
        self,
        *,
        db_path: str,
        sql: str,
        expected: Optional[Mapping[str, Any]] = None,
        min_rows: int = 1,
        max_rows: int = 200,
        timeout_ms: int = 15_000,
        timestamp_column: Optional[str] = None,
    ) -> None:
        if not isinstance(db_path, str) or not db_path.strip():
            raise ValueError("RootSqliteOracle requires non-empty db_path string")
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("RootSqliteOracle requires non-empty sql string")
        self._db_path = str(db_path)
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
                    query_type="sqlite",
                    cmd=f"shell su 0 sqlite3 -json {self._db_path} <sql>",
                    path=self._db_path,
                    sql=self._sql,
                    timeout_ms=self._timeout_ms,
                    serial=None,
                )
            ],
            result_for_digest={"missing": list(missing), "reason": reason},
            anti_gaming_notes=[
                (
                    "Hard oracle: queries app database state directly on device via root; "
                    "robust to UI spoofing."
                ),
            ],
            decision=make_decision(success=False, score=0.0, reason=reason, conclusive=False),
            capabilities_required=list(self.capabilities_required),
            missing_capabilities=list(missing),
        )

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller

        if not hasattr(controller, "root_shell"):
            return [
                self._missing_cap_event(
                    phase="post",
                    missing=["root_shell"],
                    reason="missing controller capability: root_shell",
                )
            ]

        cmd = " ".join(
            (
                "sqlite3",
                "-json",
                shlex.quote(self._db_path),
                shlex.quote(self._sql),
            )
        )

        res: Any
        try:
            res = controller.root_shell(
                cmd, timeout_s=float(self._timeout_ms) / 1000.0, check=False
            )
        except TypeError:
            res = controller.root_shell(cmd, check=False)
        except Exception as e:  # pragma: no cover
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="sqlite",
                            cmd=f"shell su 0 {cmd}",
                            path=self._db_path,
                            sql=self._sql,
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"exception": repr(e), "cmd": cmd},
                    anti_gaming_notes=[
                        "Hard oracle: root sqlite query exceptions are treated as inconclusive.",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="root sqlite query raised exception",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        stdout = str(getattr(res, "stdout", "") or "") if hasattr(res, "stdout") else str(res)
        stderr = getattr(res, "stderr", None) if hasattr(res, "stderr") else None
        returncode = getattr(res, "returncode", None) if hasattr(res, "returncode") else 0

        meta = {
            "args": getattr(res, "args", None) if hasattr(res, "args") else None,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

        queries = [
            make_query(
                query_type="sqlite",
                cmd=f"shell su 0 {cmd}",
                path=self._db_path,
                sql=self._sql,
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
            )
        ]

        if isinstance(returncode, int) and returncode != 0:
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=queries,
                    result_for_digest={"cmd": cmd, "meta": meta},
                    result_preview={"returncode": returncode, "stderr": str(stderr or "")[:200]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: root/sqlite3 may be unavailable in some environments; "
                            "non-zero return codes are treated as inconclusive."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="root sqlite3 command failed",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        if _SQLITE_ERROR_RE.search(stdout):
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=queries,
                    result_for_digest={"cmd": cmd, "meta": meta},
                    result_preview={"stdout_preview": stdout[:200]},
                    anti_gaming_notes=[
                        "Hard oracle: sqlite3 output indicates an error; treated as inconclusive.",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="sqlite3 reported error",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        try:
            parsed = json.loads(stdout) if stdout.strip() else []
        except Exception as e:
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=queries,
                    result_for_digest={"cmd": cmd, "meta": meta, "parse_error": repr(e)},
                    result_preview={"parse_error": repr(e), "stdout_preview": stdout[:200]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: expects `sqlite3 -json` output; parsing failures are "
                            "treated as inconclusive."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="failed to parse sqlite3 -json output",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        rows: List[Dict[str, Any]] = []
        if isinstance(parsed, list):
            for item in parsed[: self._max_rows]:
                if isinstance(item, dict):
                    rows.append({str(k): v for k, v in item.items()})
        elif isinstance(parsed, dict):
            rows.append({str(k): v for k, v in parsed.items()})

        truncated = isinstance(parsed, list) and len(parsed) > self._max_rows

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

            parsed_count = 0
            in_window_count = 0
            filtered: List[Dict[str, Any]] = []
            for row in rows:
                ts_ms = _parse_ts_ms(row.get(self._timestamp_column))
                if ts_ms is not None:
                    parsed_count += 1
                    if window.contains(ts_ms):
                        in_window_count += 1
                        filtered.append(row)
            candidate_rows = filtered
            timestamp_stats = {
                "timestamp_column": self._timestamp_column,
                "parsed_count": parsed_count,
                "in_window_count": in_window_count,
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
        if self._expected:
            reason = (
                f"matched {len(matches)} row(s) with expected fields"
                if success
                else "no matching rows"
            )
        else:
            reason = f"returned {len(candidate_rows)} row(s)" if success else "no rows returned"

        result_for_digest = {
            "db_path": self._db_path,
            "sql": self._sql,
            "expected": self._expected,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "max_rows": self._max_rows,
            "min_rows": self._min_rows,
            "timestamp_column": self._timestamp_column,
            "timestamp_stats": timestamp_stats,
            "device_window": _window_meta(window) if window else None,
            "device_window_meta": window_meta,
            "cmd": cmd,
            "meta": {k: v for k, v in meta.items() if k != "stdout"},
        }

        preview = {
            "success": success,
            "row_count": len(rows),
            "candidate_row_count": len(candidate_rows),
            "match_count": len(matches),
            "expected": self._expected,
            "truncated": truncated,
            "sample_rows": matches[:3] if matches else rows[:3],
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
                    "Hard oracle: queries database state directly via root sqlite3.",
                    (
                        "Time window (optional): set timestamp_column to restrict matches to the "
                        "episode device-time window (prevents stale/historical false positives)."
                    ),
                ],
                decision=make_decision(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason=reason,
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]


@register_oracle(RootSqliteOracle.oracle_id)
def _make_root_sqlite(cfg: Mapping[str, Any]) -> Oracle:
    db_path = cfg.get("db_path") or cfg.get("path")
    sql = cfg.get("sql") or cfg.get("query")
    expected = cfg.get("expected") or cfg.get("expect") or {}
    timestamp_column = cfg.get("timestamp_column") or cfg.get("ts_column")

    if not isinstance(db_path, str) or not db_path:
        raise ValueError("RootSqliteOracle requires 'db_path' string")
    if not isinstance(sql, str) or not sql:
        raise ValueError("RootSqliteOracle requires 'sql' string")
    if expected is None:
        expected = {}
    if not isinstance(expected, Mapping):
        raise ValueError("expected/expect must be an object")

    return RootSqliteOracle(
        db_path=db_path,
        sql=sql,
        expected=dict(expected),
        min_rows=int(cfg.get("min_rows", 1)),
        max_rows=int(cfg.get("max_rows", 200)),
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
        timestamp_column=str(timestamp_column) if timestamp_column else None,
    )


@register_oracle("RootSqliteOracle")
def _make_root_sqlite_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_root_sqlite(cfg)
