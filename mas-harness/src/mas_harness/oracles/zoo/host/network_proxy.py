"""Host-side network proxy oracle.

Phase 2 Step 4.2: NetworkProxyOracle (optional enhancement: mitmproxy/tcpdump).

Goal
----
Provide a hard-ish, auditable signal that a network request was *actually sent*
by checking a host-side proxy/capture log (not UI-derived).

Design constraints (anti-gaming / reproducibility)
--------------------------------------------------
- Default disabled (`enabled: false`) and must be explicitly enabled in task spec.
- Strongly prefers *structured* proxy logs that do **not** include raw request
  bodies/headers (privacy). The oracle records only summaries + hashes.
- Time windowing: enforces episode host window via file mtime and (when present)
  per-entry `ts_ms`.
- Token/run binding: requires `token` by default, and validates it via a
  `token_sha256` / `tokens_sha256` field in the structured log entry.

Expected log format
-------------------
The oracle consumes a JSONL file under `ARTIFACTS_ROOT` (or `MAS_ARTIFACTS_ROOT`).
Each line should be a JSON object with at least:

  {
    "ts_ms": 1700000000000,
    "request": {"method": "POST", "host": "example.com", "path": "/api", "body_sha256": "<64hex>"},
    "response": {"status_code": 200},
    "token_sha256": "<sha256(token)>"
  }

Notes
- `token_sha256` is computed using `mas_harness.oracles.zoo.utils.hashing.stable_sha256(token)`.
- Optional: add `run_id_sha256` / `episode_id_sha256` for stronger binding.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

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
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256, stable_sha256
from mas_harness.oracles.zoo.utils.time_window import TimeWindow

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _artifacts_root() -> Optional[Path]:
    root = os.environ.get("ARTIFACTS_ROOT") or os.environ.get("MAS_ARTIFACTS_ROOT")
    if not root:
        return None
    return Path(root)


def _resolve_under_root(path: str) -> Tuple[Optional[Path], Optional[str]]:
    p = Path(path)
    if p.is_absolute():
        return p, None

    root = _artifacts_root()
    if root is None:
        return None, "missing ARTIFACTS_ROOT for relative proxy log path"

    try:
        resolved_root = root.resolve()
        resolved_path = (root / p).resolve()
        if resolved_path == resolved_root or not resolved_path.is_relative_to(resolved_root):
            return None, "invalid relative path (escapes ARTIFACTS_ROOT)"
        return resolved_path, None
    except Exception:
        return root / p, None


def _find_matching(glob_pattern: str) -> Sequence[Path]:
    root = _artifacts_root()
    if root is None:
        return []

    resolved_root: Path
    try:
        resolved_root = root.resolve()
    except Exception:  # pragma: no cover
        resolved_root = root

    matches: list[Path] = []
    for p in root.glob(glob_pattern):
        try:
            resolved_p = p.resolve()
        except Exception:
            resolved_p = p
        if resolved_p == resolved_root or not resolved_p.is_relative_to(resolved_root):
            continue
        if p.is_file():
            matches.append(p)
    return sorted(matches)


def _stat_mtime_ms(path: Path) -> int:
    return int(path.stat().st_mtime * 1000)


def _pick_latest_in_window(paths: Sequence[Path], window: TimeWindow) -> Optional[Path]:
    candidates = [p for p in paths if p.exists() and window.contains(_stat_mtime_ms(p))]
    if not candidates:
        return None
    return max(candidates, key=_stat_mtime_ms)


def _write_json_artifact(
    ctx: OracleContext, *, rel_path: Path, obj: Any
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if ctx.episode_dir is None:
        return None, "missing episode_dir (cannot persist artifact)"
    try:
        out_path = ctx.episode_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        out_path.write_bytes(data)
        sha256 = stable_file_sha256(out_path)
        return (
            {
                "path": rel_path.as_posix(),
                "sha256": sha256,
                "bytes": len(data),
                "mime": "application/json",
            },
            None,
        )
    except Exception as e:  # pragma: no cover
        return None, f"artifact_write_failed:{type(e).__name__}:{e}"


def _get_by_path(obj: Any, path: str) -> Tuple[bool, Any]:
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
            continue
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(cur):
                cur = cur[idx]
                continue
        return False, None
    return True, cur


def _fingerprint_value(value: Any) -> Dict[str, Any]:
    if value is None or isinstance(value, (bool, int, float)):
        return {"type": type(value).__name__, "value": value}
    if isinstance(value, str):
        return {"type": "str", "len": len(value), "sha256": stable_sha256(value)}
    return {"type": type(value).__name__, "sha256": stable_sha256(value)}


def _match_expected(obj: Any, expected: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    matched: Dict[str, Any] = {}
    mismatches: Dict[str, Any] = {}
    for key, exp in expected.items():
        found, got = _get_by_path(obj, str(key))
        if found and got == exp:
            matched[str(key)] = _fingerprint_value(got)
        else:
            mismatches[str(key)] = {
                "expected": _fingerprint_value(exp),
                "got": _fingerprint_value(got) if found else None,
                "found": found,
            }
    return matched, mismatches


def _is_sha256_hex(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    return bool(_SHA256_HEX_RE.match(s.strip().lower()))


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        return int(v)
    except Exception:
        return None


def _truncate_file(path: Path) -> Optional[str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8"):
            pass
        return None
    except Exception as e:  # pragma: no cover
        return f"{type(e).__name__}:{e}"


class NetworkProxyOracle(Oracle):
    oracle_id = "network_proxy"
    oracle_name = "network_proxy"
    oracle_type = "hard"
    capabilities_required = ("host_artifacts_required",)

    def __init__(
        self,
        *,
        enabled: bool = False,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        clear_before_run: bool = True,
        clear_mode: str = "truncate",
        token: Optional[str] = None,
        require_token: bool = True,
        run_id: Optional[str] = None,
        episode_id: Optional[str] = None,
        expected: Optional[Mapping[str, Any]] = None,
        timestamp_path: str = "ts_ms",
        request_method_path: str = "request.method",
        request_host_path: str = "request.host",
        request_path_path: str = "request.path",
        request_body_sha256_path: str = "request.body_sha256",
        response_status_code_path: str = "response.status_code",
        token_sha256_path: str = "token_sha256",
        tokens_sha256_path: str = "tokens_sha256",
        run_id_sha256_path: str = "run_id_sha256",
        episode_id_sha256_path: str = "episode_id_sha256",
        method: Optional[str] = None,
        host: Optional[str] = None,
        path_match: Optional[str] = None,
        status_min: Optional[int] = 200,
        status_max: Optional[int] = 399,
        max_events: int = 5000,
        store_artifact: bool = True,
    ) -> None:
        if (not path) == (not glob):
            raise ValueError("NetworkProxyOracle requires exactly one of: path, glob")
        self._enabled = bool(enabled)
        self._path = str(path) if path else None
        self._glob = str(glob) if glob else None
        self._clear = bool(clear_before_run)
        self._clear_mode = str(clear_mode or "truncate").strip().lower()

        self._token = str(token) if token is not None else None
        self._require_token = bool(require_token)
        self._run_id = str(run_id) if run_id is not None else None
        self._episode_id = str(episode_id) if episode_id is not None else None

        self._expected = dict(expected) if expected else {}

        self._timestamp_path = str(timestamp_path)
        self._request_method_path = str(request_method_path)
        self._request_host_path = str(request_host_path)
        self._request_path_path = str(request_path_path)
        self._request_body_sha256_path = str(request_body_sha256_path)
        self._response_status_code_path = str(response_status_code_path)
        self._token_sha256_path = str(token_sha256_path)
        self._tokens_sha256_path = str(tokens_sha256_path)
        self._run_id_sha256_path = str(run_id_sha256_path)
        self._episode_id_sha256_path = str(episode_id_sha256_path)

        self._method = str(method).upper() if method else None
        self._host = str(host).lower() if host else None
        self._path_match = str(path_match).strip().lower() if path_match else None

        self._status_min = int(status_min) if status_min is not None else None
        self._status_max = int(status_max) if status_max is not None else None
        self._max_events = max(1, int(max_events))
        self._store_artifact = bool(store_artifact)

        if self._clear_mode not in {"truncate", "delete"}:
            raise ValueError("clear_mode must be one of: truncate, delete")

    def _missing_host_capability_event(self, *, phase: str, reason: str) -> Dict[str, Any]:
        reason = str(reason)
        if "host_artifacts_required" not in reason:
            reason = f"missing host_artifacts_required: {reason}"
        return make_oracle_event(
            ts_ms=now_ms(),
            oracle_id=self.oracle_id,
            oracle_name=self.oracle_name,
            oracle_type=self.oracle_type,
            phase=phase,
            queries=[
                make_query(
                    query_type="host_file",
                    path=str(self._path or self._glob or "ARTIFACTS_ROOT"),
                    timeout_ms=0,
                    serial=None,
                )
            ],
            result_for_digest={"missing": ["host_artifacts_required"], "reason": reason},
            anti_gaming_notes=[
                (
                    "Optional oracle: reads a host-side proxy log; requires ARTIFACTS_ROOT "
                    "to be set."
                ),
            ],
            decision=make_decision(
                success=False,
                score=0.0,
                reason=reason,
                conclusive=False,
            ),
            capabilities_required=list(self.capabilities_required),
            missing_capabilities=["host_artifacts_required"],
        )

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        del ctx
        if not self._enabled or not self._clear:
            return []

        removed: list[str] = []
        truncated: list[str] = []
        errors: list[str] = []

        targets: Sequence[Path]
        if self._path is not None:
            resolved, error = _resolve_under_root(self._path)
            if error is not None:
                return [self._missing_host_capability_event(phase="pre", reason=error)]
            targets = [resolved] if resolved is not None else []
        else:
            if _artifacts_root() is None:
                return [
                    self._missing_host_capability_event(
                        phase="pre", reason="missing ARTIFACTS_ROOT for glob-based proxy logs"
                    )
                ]
            targets = _find_matching(str(self._glob))

        for p in targets:
            try:
                if not p.exists():
                    continue
                if self._clear_mode == "delete":
                    p.unlink()
                    removed.append(str(p))
                else:
                    err = _truncate_file(p)
                    if err is None:
                        truncated.append(str(p))
                    else:
                        errors.append(f"{p}: {err}")
            except Exception as e:  # pragma: no cover
                errors.append(f"{p}: {type(e).__name__}:{e}")

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="pre",
                queries=[
                    make_query(
                        query_type="host_file",
                        path=str(self._path or self._glob or ""),
                        timeout_ms=0,
                        serial=None,
                        op="clear_before_run",
                        mode=self._clear_mode,
                    )
                ],
                result_for_digest={
                    "removed": removed,
                    "truncated": truncated,
                    "errors": errors,
                },
                result_preview={
                    "removed_count": len(removed),
                    "truncated_count": len(truncated),
                    "errors": errors[:3],
                },
                anti_gaming_notes=[
                    (
                        "Pollution control: pre_check clears proxy logs to prevent stale "
                        "captures from passing within slack windows."
                    ),
                ],
                decision=make_decision(
                    success=not errors,
                    score=1.0 if not errors else 0.0,
                    reason="cleared proxy logs"
                    if not errors
                    else "failed to clear some proxy logs",
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        if not self._enabled:
            result = {"enabled": False}
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(self._path or self._glob or "ARTIFACTS_ROOT"),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        "Default-off oracle: set `enabled: true` to use proxy-based receipts.",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing network_proxy_enabled (oracle disabled; set enabled=true)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["network_proxy_enabled"],
                )
            ]

        if ctx.episode_time is None:
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(self._path or self._glob or "ARTIFACTS_ROOT"),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        (
                            "Hard-ish oracle: proxy logs must be time-windowed; requires an "
                            "episode time anchor to avoid stale/historical passes."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing episode time anchor (time window unavailable)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["episode_time_anchor"],
                )
            ]

        if self._require_token and (self._token is None or self._token == ""):
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(self._path or self._glob or "ARTIFACTS_ROOT"),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest={"missing": ["token"]},
                    anti_gaming_notes=[
                        (
                            "Anti-gaming: NetworkProxyOracle requires a per-episode token to "
                            "bind proxy events to this run."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing token (anti-gaming requirement)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["token_required"],
                )
            ]

        host_window = ctx.episode_time.host_window()

        candidate: Optional[Path]
        if self._path is not None:
            resolved, error = _resolve_under_root(self._path)
            if error is not None:
                return [self._missing_host_capability_event(phase="post", reason=error)]
            candidate = resolved
        else:
            if _artifacts_root() is None:
                return [
                    self._missing_host_capability_event(
                        phase="post", reason="missing ARTIFACTS_ROOT for glob-based proxy logs"
                    )
                ]
            candidate = _pick_latest_in_window(_find_matching(str(self._glob)), host_window)

        if candidate is None or not candidate.exists() or not candidate.is_file():
            result = {
                "path": str(candidate) if candidate else None,
                "exists": False,
                "host_window": {
                    "start_ms": host_window.start_ms,
                    "end_ms": host_window.end_ms,
                    "t0_ms": host_window.t0_ms,
                    "slack_ms": host_window.slack_ms,
                },
            }
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(candidate) if candidate else str(self._glob or ""),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        (
                            "Optional oracle: validates a host-side proxy capture log "
                            "(UI spoof-resistant)."
                        ),
                        (
                            "Time window: only logs updated during the episode host window "
                            "are considered (prevents stale/historical false positives)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing proxy log in episode time window",
                        conclusive=True,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        mtime_ms = _stat_mtime_ms(candidate)
        within_window = host_window.contains(mtime_ms)
        if not within_window:
            result = {
                "path": str(candidate),
                "exists": True,
                "mtime_ms": mtime_ms,
                "within_time_window": False,
                "host_window": {
                    "start_ms": host_window.start_ms,
                    "end_ms": host_window.end_ms,
                    "t0_ms": host_window.t0_ms,
                    "slack_ms": host_window.slack_ms,
                },
            }
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(candidate),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        (
                            "Time window: proxy log exists but is outside the episode host window "
                            "(treated as stale to prevent false positives)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="proxy log stale (outside episode time window)",
                        conclusive=True,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        raw_bytes = candidate.read_bytes()
        raw_sha256 = stable_sha256(raw_bytes)
        file_sha256 = stable_file_sha256(candidate)

        token = self._token or ""
        token_sha256 = stable_sha256(token)

        run_id_sha256 = stable_sha256(self._run_id) if self._run_id else None
        episode_id_sha256 = stable_sha256(self._episode_id) if self._episode_id else None

        parsed = 0
        parse_errors = 0
        in_window = 0
        schema_seen: Dict[str, bool] = {}

        matched_idx: Optional[int] = None
        matched_event: Optional[Dict[str, Any]] = None
        matched_fields: Dict[str, Any] = {}
        mismatches: Dict[str, Any] = {}

        token_schema_ok_any = False
        token_ok_any = False

        run_id_schema_ok_any = run_id_sha256 is None
        run_id_ok_any = run_id_sha256 is None
        episode_id_schema_ok_any = episode_id_sha256 is None
        episode_id_ok_any = episode_id_sha256 is None

        def _mark_seen(key: str) -> None:
            if key:
                schema_seen[key] = True

        # Stream parse jsonl to avoid holding large logs in memory.
        try:
            with candidate.open("r", encoding="utf-8", errors="replace") as f:
                for line_idx, line in enumerate(f, start=1):
                    if parsed >= self._max_events:
                        break
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        parse_errors += 1
                        continue
                    if not isinstance(obj, dict):
                        parse_errors += 1
                        continue
                    parsed += 1

                    found_ts, ts_val = _get_by_path(obj, self._timestamp_path)
                    ts_ms = _safe_int(ts_val) if found_ts else None
                    if ts_ms is None:
                        continue
                    _mark_seen(self._timestamp_path)
                    if not host_window.contains(ts_ms):
                        continue
                    in_window += 1

                    # Token binding: accept either token_sha256 or tokens_sha256 list.
                    found_token, got_token = _get_by_path(obj, self._token_sha256_path)
                    found_tokens, got_tokens = _get_by_path(obj, self._tokens_sha256_path)
                    token_schema_ok = False
                    token_ok = False
                    if found_token and _is_sha256_hex(got_token):
                        token_schema_ok = True
                        token_ok = str(got_token).lower() == token_sha256
                    elif found_tokens and isinstance(got_tokens, list):
                        token_schema_ok = any(_is_sha256_hex(x) for x in got_tokens)
                        token_ok = any(
                            isinstance(x, str) and x.lower() == token_sha256 for x in got_tokens
                        )
                    token_schema_ok_any = token_schema_ok_any or token_schema_ok
                    token_ok_any = token_ok_any or token_ok
                    if token_schema_ok:
                        _mark_seen("token_sha256")

                    # Optional run/episode binding (hash-only; per-entry).
                    run_id_ok = True
                    if run_id_sha256 is not None:
                        found_run, got_run = _get_by_path(obj, self._run_id_sha256_path)
                        if found_run and _is_sha256_hex(got_run):
                            run_id_schema_ok_any = True
                            _mark_seen(self._run_id_sha256_path)
                            run_id_ok = str(got_run).lower() == run_id_sha256
                            run_id_ok_any = run_id_ok_any or run_id_ok
                        else:
                            run_id_ok = False

                    episode_id_ok = True
                    if episode_id_sha256 is not None:
                        found_ep, got_ep = _get_by_path(obj, self._episode_id_sha256_path)
                        if found_ep and _is_sha256_hex(got_ep):
                            episode_id_schema_ok_any = True
                            _mark_seen(self._episode_id_sha256_path)
                            episode_id_ok = str(got_ep).lower() == episode_id_sha256
                            episode_id_ok_any = episode_id_ok_any or episode_id_ok
                        else:
                            episode_id_ok = False

                    # Required structured fields.
                    found_method, method_val = _get_by_path(obj, self._request_method_path)
                    found_host, host_val = _get_by_path(obj, self._request_host_path)
                    found_path, path_val = _get_by_path(obj, self._request_path_path)
                    found_body, body_val = _get_by_path(obj, self._request_body_sha256_path)
                    found_status, status_val = _get_by_path(obj, self._response_status_code_path)

                    if found_method:
                        _mark_seen(self._request_method_path)
                    if found_host:
                        _mark_seen(self._request_host_path)
                    if found_path:
                        _mark_seen(self._request_path_path)
                    if found_body:
                        _mark_seen(self._request_body_sha256_path)
                    if found_status:
                        _mark_seen(self._response_status_code_path)

                    if not (
                        found_method and found_host and found_path and found_body and found_status
                    ):
                        continue

                    method_s = str(method_val).upper()
                    host_s = str(host_val).lower()
                    path_s = str(path_val)
                    body_sha = str(body_val).lower()
                    status_code = _safe_int(status_val)

                    if not _is_sha256_hex(body_sha):
                        continue
                    _mark_seen(self._request_body_sha256_path + " (sha256)")

                    if status_code is None:
                        continue
                    _mark_seen(self._response_status_code_path + " (int)")

                    # Optional user filters.
                    if self._method is not None and method_s != self._method:
                        continue
                    if self._host is not None and host_s != self._host:
                        continue
                    if self._path_match:
                        needle = str(self._path_match)
                        if needle.startswith("contains:"):
                            if needle.split(":", 1)[1] not in path_s:
                                continue
                        elif needle.startswith("prefix:"):
                            if not path_s.startswith(needle.split(":", 1)[1]):
                                continue
                        elif needle.startswith("equals:"):
                            if path_s != needle.split(":", 1)[1]:
                                continue
                        elif needle.startswith("regex:"):
                            pat = needle.split(":", 1)[1]
                            try:
                                if re.search(pat, path_s) is None:
                                    continue
                            except re.error:
                                continue

                    if self._status_min is not None and status_code < self._status_min:
                        continue
                    if self._status_max is not None and status_code > self._status_max:
                        continue

                    entry_matched, entry_mismatches = _match_expected(obj, self._expected)

                    entry_ok = token_ok and run_id_ok and episode_id_ok and not entry_mismatches
                    if entry_ok:
                        matched_idx = line_idx
                        matched_event = {
                            "ts_ms": ts_ms,
                            "request": {
                                "method": method_s,
                                "host": host_s,
                                "path": path_s,
                                "body_sha256": body_sha,
                            },
                            "response": {"status_code": status_code},
                            "token_sha256": token_sha256,
                        }
                        if run_id_sha256 is not None:
                            matched_event["run_id_sha256"] = run_id_sha256
                        if episode_id_sha256 is not None:
                            matched_event["episode_id_sha256"] = episode_id_sha256
                        matched_fields = entry_matched
                        mismatches = entry_mismatches
                        break

                    # Keep the first mismatches for debugging (hash-only).
                    if matched_idx is None and not mismatches:
                        mismatches = entry_mismatches
                        matched_fields = entry_matched

        except Exception as e:
            result = {
                "path": str(candidate),
                "raw_sha256": raw_sha256,
                "file_sha256": file_sha256,
                "read_error": f"{type(e).__name__}:{e}",
            }
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(candidate),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        "Proxy log read/parse failures are treated as inconclusive.",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="failed to read/parse proxy log",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        ok = matched_event is not None

        required_schema_keys = [
            self._timestamp_path,
            self._request_method_path,
            self._request_host_path,
            self._request_path_path,
            self._request_body_sha256_path + " (sha256)",
            self._response_status_code_path + " (int)",
            "token_sha256",
        ]
        if run_id_sha256 is not None:
            required_schema_keys.append(self._run_id_sha256_path)
        if episode_id_sha256 is not None:
            required_schema_keys.append(self._episode_id_sha256_path)

        missing_required = [k for k in required_schema_keys if not schema_seen.get(k)]

        # Conclusiveness: if schema is missing required token/run binding fields, return
        # inconclusive.
        conclusive: bool
        reason: str
        if ok:
            conclusive = True
            reason = f"matched proxy event (line={matched_idx})"
        elif parse_errors and parsed == 0:
            conclusive = False
            reason = "proxy log jsonl parse failed"
        elif in_window == 0:
            conclusive = True
            reason = "no proxy events within episode time window"
        elif missing_required:
            conclusive = False
            reason = "proxy log schema missing required fields"
        elif not token_schema_ok_any:
            conclusive = False
            reason = "proxy log missing token_sha256/tokens_sha256 fields"
        elif run_id_sha256 is not None and not run_id_schema_ok_any:
            conclusive = False
            reason = "proxy log missing run_id_sha256 field"
        elif episode_id_sha256 is not None and not episode_id_schema_ok_any:
            conclusive = False
            reason = "proxy log missing episode_id_sha256 field"
        else:
            conclusive = True
            if not token_ok_any:
                reason = "token not found in proxy events"
            elif run_id_sha256 is not None and not run_id_ok_any:
                reason = "run_id not found in proxy events"
            elif episode_id_sha256 is not None and not episode_id_ok_any:
                reason = "episode_id not found in proxy events"
            elif mismatches:
                reason = "expected fields did not match"
            else:
                reason = "no proxy event matched filters"

        result_preview: Dict[str, Any] = {
            "path": str(candidate),
            "file_sha256": file_sha256,
            "raw_sha256": raw_sha256,
            "mtime_ms": mtime_ms,
            "within_time_window": within_window,
            "host_window": {
                "start_ms": host_window.start_ms,
                "end_ms": host_window.end_ms,
                "t0_ms": host_window.t0_ms,
                "slack_ms": host_window.slack_ms,
            },
            "parse": {
                "max_events": self._max_events,
                "parsed_events": parsed,
                "parse_errors": parse_errors,
                "in_window_events": in_window,
            },
            "binding": {
                "token_sha256": token_sha256,
                "token_schema_ok": token_schema_ok_any,
                "token_ok_any": token_ok_any,
                "run_id_sha256": run_id_sha256,
                "episode_id_sha256": episode_id_sha256,
            },
            "schema_seen": dict(sorted(schema_seen.items())) if schema_seen else {},
            "schema_missing_required": missing_required,
            "expected_keys": sorted(str(k) for k in self._expected.keys()),
            "matched_fields": matched_fields,
            "mismatches": mismatches,
            "matched_event": matched_event,
        }

        artifact: Optional[Dict[str, Any]] = None
        artifact_error: Optional[str] = None
        if self._store_artifact:
            rel_name = (candidate.name or "network_proxy.jsonl").replace("/", "_")
            artifact_rel = Path("oracle_artifacts") / f"network_proxy_post_{rel_name}.json"
            artifact_obj = {
                "schema": "network_proxy_redacted_v1",
                "source": {
                    "path": str(candidate),
                    "file_sha256": file_sha256,
                    "raw_sha256": raw_sha256,
                    "mtime_ms": mtime_ms,
                },
                "host_window": result_preview["host_window"],
                "parse": result_preview["parse"],
                "binding": result_preview["binding"],
                "matched_event": matched_event,
                "expected_keys": result_preview["expected_keys"],
                "matched_fields": matched_fields,
                "mismatches": mismatches,
            }
            artifact, artifact_error = _write_json_artifact(
                ctx, rel_path=artifact_rel, obj=artifact_obj
            )
            if artifact is not None:
                result_preview["artifact"] = artifact
            if artifact_error is not None:
                result_preview["artifact_error"] = artifact_error

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[
                    make_query(
                        query_type="host_file",
                        path=str(candidate),
                        timeout_ms=0,
                        serial=None,
                    )
                ],
                result_for_digest=result_preview,
                result_preview=result_preview,
                anti_gaming_notes=[
                    (
                        "Optional hard-ish oracle: checks host-side proxy logs for an actual "
                        "network request (UI spoof-resistant)."
                    ),
                    (
                        "Token/run binding: requires token_sha256 "
                        "(and optionally run_id/episode_id sha256) "
                        "to bind proxy events to this episode."
                    ),
                    (
                        "Time window: considers only logs/events within the episode host window "
                        "(prevents stale/historical false positives)."
                    ),
                    (
                        "Privacy: evidence records only method/host/path summary + body hash + "
                        "status code; raw payloads are not stored."
                    ),
                    "Default off: requires `enabled: true` in task spec.",
                ],
                decision=make_decision(
                    success=ok,
                    score=1.0 if ok else 0.0,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
                artifacts=[artifact] if artifact is not None else None,
            )
        ]


@register_oracle(NetworkProxyOracle.oracle_id)
def _make_network_proxy(cfg: Mapping[str, Any]) -> Oracle:
    enabled = bool(cfg.get("enabled", False))
    path = cfg.get("path")
    glob = cfg.get("glob") or cfg.get("pattern")
    clear_before_run = bool(cfg.get("clear_before_run", True))
    clear_mode = cfg.get("clear_mode", "truncate")
    token = cfg.get("token")
    require_token = bool(cfg.get("require_token", True))
    run_id = cfg.get("run_id")
    episode_id = cfg.get("episode_id")
    expected = cfg.get("expected") or cfg.get("expect") or {}

    timestamp_path = cfg.get("timestamp_path", "ts_ms")
    request_method_path = cfg.get("request_method_path", "request.method")
    request_host_path = cfg.get("request_host_path", "request.host")
    request_path_path = cfg.get("request_path_path", "request.path")
    request_body_sha256_path = cfg.get("request_body_sha256_path", "request.body_sha256")
    response_status_code_path = cfg.get("response_status_code_path", "response.status_code")
    token_sha256_path = cfg.get("token_sha256_path", "token_sha256")
    tokens_sha256_path = cfg.get("tokens_sha256_path", "tokens_sha256")
    run_id_sha256_path = cfg.get("run_id_sha256_path", "run_id_sha256")
    episode_id_sha256_path = cfg.get("episode_id_sha256_path", "episode_id_sha256")

    method = cfg.get("method")
    host = cfg.get("host")
    path_match = cfg.get("path_match")

    status_min = cfg.get("status_min", 200)
    status_max = cfg.get("status_max", 399)
    max_events = cfg.get("max_events", 5000)
    store_artifact = bool(cfg.get("store_artifact", True))

    if expected is None:
        expected = {}
    if not isinstance(expected, Mapping):
        raise ValueError("expected/expect must be an object")

    return NetworkProxyOracle(
        enabled=enabled,
        path=str(path) if path is not None else None,
        glob=str(glob) if glob is not None else None,
        clear_before_run=clear_before_run,
        clear_mode=str(clear_mode),
        token=str(token) if token is not None else None,
        require_token=require_token,
        run_id=str(run_id) if run_id is not None else None,
        episode_id=str(episode_id) if episode_id is not None else None,
        expected=dict(expected),
        timestamp_path=str(timestamp_path),
        request_method_path=str(request_method_path),
        request_host_path=str(request_host_path),
        request_path_path=str(request_path_path),
        request_body_sha256_path=str(request_body_sha256_path),
        response_status_code_path=str(response_status_code_path),
        token_sha256_path=str(token_sha256_path),
        tokens_sha256_path=str(tokens_sha256_path),
        run_id_sha256_path=str(run_id_sha256_path),
        episode_id_sha256_path=str(episode_id_sha256_path),
        method=str(method) if method is not None else None,
        host=str(host) if host is not None else None,
        path_match=str(path_match) if path_match is not None else None,
        status_min=int(status_min) if status_min is not None else None,
        status_max=int(status_max) if status_max is not None else None,
        max_events=int(max_events),
        store_artifact=store_artifact,
    )


@register_oracle("NetworkProxyOracle")
def _make_network_proxy_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_network_proxy(cfg)
