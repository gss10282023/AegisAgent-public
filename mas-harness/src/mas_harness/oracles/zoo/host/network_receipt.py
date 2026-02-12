"""Host-side network receipt oracle.

Phase 2 Step 4.1: NetworkReceiptOracle (service/server/host receipts).

This oracle is intended to make "submit/upload/API call" tasks have a hard,
auditable answer even when the device does not persist results locally.

Data source
-----------
- A test backend / mock service writes a JSON "receipt" file under ARTIFACTS_ROOT.

Guarantees / anti-gaming
------------------------
- Enforces episode time window using the host file mtime.
- Optional pre_check deletion (enabled by default) to prevent stale receipts from
  passing within the slack window.
- Requires a per-episode token and verifies it appears in request body/header/query
  (scope-based; avoids trusting UI-only signals).
- Evidence is privacy-preserving: only summaries/hashes are recorded; raw receipt
  payloads are not copied into the evidence bundle.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
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
        return None, "missing ARTIFACTS_ROOT for relative host receipt path"

    # Prevent path traversal outside ARTIFACTS_ROOT.
    try:
        resolved_root = root.resolve()
        resolved_path = (root / p).resolve()
        if resolved_path == resolved_root or not resolved_path.is_relative_to(resolved_root):
            return None, "invalid relative path (escapes ARTIFACTS_ROOT)"
        return resolved_path, None
    except Exception:
        # Best-effort: keep a safe join if resolve fails.
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


@dataclass(frozen=True)
class _TokenMatch:
    ok: bool
    schema_ok: bool
    detail: Dict[str, Any]


def _match_token(
    obj: Any,
    *,
    token: str,
    token_path: Optional[str],
    token_match: str,
    token_scopes: Sequence[str],
) -> _TokenMatch:
    mode = str(token_match or "equals").strip().lower()
    if token_path:
        found, got = _get_by_path(obj, str(token_path))
        got_str = "" if got is None else str(got)
        ok = token in got_str if mode == "contains" else got_str == token
        return _TokenMatch(
            ok=ok,
            schema_ok=found,
            detail={
                "enabled": True,
                "mode": mode,
                "token_sha256": stable_sha256(token),
                "token_path": token_path,
                "found": found,
                "got_fingerprint": _fingerprint_value(got_str) if found else None,
            },
        )

    scopes = list(token_scopes)
    present: list[dict[str, Any]] = []
    for scope in scopes:
        found, scoped_obj = _get_by_path(obj, str(scope))
        if not found:
            continue
        # Search within canonical JSON encoding; this supports nested dict/list bodies.
        try:
            haystack = json.dumps(scoped_obj, sort_keys=True, ensure_ascii=False)
        except Exception:
            haystack = str(scoped_obj)
        hit = token in haystack
        present.append(
            {
                "scope": str(scope),
                "hit": bool(hit),
                "scoped_sha256": stable_sha256(scoped_obj),
            }
        )
        if hit:
            return _TokenMatch(
                ok=True,
                schema_ok=True,
                detail={
                    "enabled": True,
                    "mode": "scoped_contains",
                    "token_sha256": stable_sha256(token),
                    "matched_scope": str(scope),
                    "scoped_sha256": stable_sha256(scoped_obj),
                    "scopes_checked": len(present),
                },
            )

    return _TokenMatch(
        ok=False,
        schema_ok=bool(present),
        detail={
            "enabled": True,
            "mode": "scoped_contains",
            "token_sha256": stable_sha256(token),
            "matched_scope": None,
            "scopes_checked": len(present),
            "scopes_present": present[:5],
            "note": "token not found in configured request scopes",
        },
    )


class NetworkReceiptOracle(Oracle):
    oracle_id = "network_receipt"
    oracle_name = "network_receipt"
    oracle_type = "hard"
    capabilities_required = ("host_artifacts_required",)

    def __init__(
        self,
        *,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        expected: Optional[Mapping[str, Any]] = None,
        token: Optional[str] = None,
        token_path: Optional[str] = None,
        token_match: str = "equals",
        token_scopes: Optional[Sequence[str]] = None,
        clear_before_run: bool = True,
        store_artifact: bool = True,
        require_token: bool = True,
    ) -> None:
        if (not path) == (not glob):
            raise ValueError("NetworkReceiptOracle requires exactly one of: path, glob")
        self._path = str(path) if path else None
        self._glob = str(glob) if glob else None
        self._expected = dict(expected) if expected else {}
        self._token = str(token) if token is not None else None
        self._token_path = str(token_path) if token_path else None
        self._token_match = str(token_match or "equals")
        self._token_scopes = tuple(
            str(x)
            for x in (
                token_scopes
                if token_scopes is not None
                else (
                    "request.body",
                    "request.headers",
                    "request.query",
                    "body",
                    "headers",
                    "query",
                )
            )
            if str(x).strip()
        )
        self._clear = bool(clear_before_run)
        self._store_artifact = bool(store_artifact)
        self._require_token = bool(require_token)

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
                    "Hard oracle: reads a host-side network receipt written by a trusted "
                    "service; requires ARTIFACTS_ROOT to be set."
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
        if not self._clear:
            return []

        removed: list[str] = []
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
                        phase="pre", reason="missing ARTIFACTS_ROOT for glob-based network receipt"
                    )
                ]
            targets = _find_matching(str(self._glob))

        for p in targets:
            try:
                if p.exists():
                    p.unlink()
                    removed.append(str(p))
            except Exception as e:  # pragma: no cover
                errors.append(f"{p}: {e}")

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
                    )
                ],
                result_for_digest={"removed": removed, "errors": errors},
                result_preview={"removed_count": len(removed), "errors": errors[:3]},
                anti_gaming_notes=[
                    (
                        "Pollution control: pre_check deletes stale host receipts to prevent "
                        "false positives within slack windows."
                    ),
                ],
                decision=make_decision(
                    success=not errors,
                    score=1.0 if not errors else 0.0,
                    reason="cleared receipts" if not errors else "failed to clear some receipts",
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
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
                            "Hard oracle: host-side receipts must be time-windowed; requires "
                            "an episode time anchor to avoid stale/historical passes."
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
                            "Anti-gaming: NetworkReceiptOracle requires a per-episode token to "
                            "avoid historical/polluted passes."
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
                        phase="post", reason="missing ARTIFACTS_ROOT for glob-based network receipt"
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
                            "Hard oracle: validates a host-side receipt emitted by a trusted "
                            "backend (UI spoof-resistant)."
                        ),
                        (
                            "Time window: only receipts created during the episode host window "
                            "are considered (prevents stale/historical false positives)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing network receipt json in episode time window",
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
                            "Time window: receipt exists but is outside the episode host window "
                            "(treated as stale to prevent false positives)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="network receipt stale (outside episode time window)",
                        conclusive=True,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        raw_bytes = candidate.read_bytes()
        raw_sha256 = stable_sha256(raw_bytes)

        try:
            receipt_obj = json.loads(raw_bytes.decode("utf-8"))
        except Exception as e:
            result = {
                "path": str(candidate),
                "raw_sha256": raw_sha256,
                "parse_error": repr(e),
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
                            "Hard oracle: receipt JSON must be machine-parseable; parse failures "
                            "are treated as inconclusive."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="network receipt json parse failed",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        if isinstance(receipt_obj, dict):
            entries = [receipt_obj]
        elif isinstance(receipt_obj, list):
            entries = [x for x in receipt_obj if isinstance(x, dict)]
        else:
            entries = []

        if not entries:
            result = {
                "path": str(candidate),
                "raw_sha256": raw_sha256,
                "format": type(receipt_obj).__name__,
                "entry_count": 0,
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
                            "Hard oracle: receipt JSON must contain an object or list of objects; "
                            "unknown formats are treated as inconclusive."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="network receipt json did not contain an object/list of objects",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        token = self._token or ""

        matched_idx: Optional[int] = None
        matched_fields: Dict[str, Any] = {}
        mismatches: Dict[str, Any] = {}
        token_detail: Dict[str, Any] = {"enabled": True}
        token_schema_ok_any = False
        token_ok_any = False

        for idx, entry in enumerate(entries):
            token_match = _match_token(
                entry,
                token=token,
                token_path=self._token_path,
                token_match=self._token_match,
                token_scopes=self._token_scopes,
            )
            token_schema_ok_any = token_schema_ok_any or token_match.schema_ok
            token_ok_any = token_ok_any or token_match.ok

            entry_matched, entry_mismatches = _match_expected(entry, self._expected)
            entry_ok = token_match.ok and not entry_mismatches
            if entry_ok:
                matched_idx = idx
                matched_fields = entry_matched
                mismatches = entry_mismatches
                token_detail = {"ok": True, **token_match.detail}
                break

            # Keep the first token detail for debugging (hashes only).
            if idx == 0:
                token_detail = {"ok": token_match.ok, **token_match.detail}
                mismatches = entry_mismatches
                matched_fields = entry_matched

        ok = matched_idx is not None

        conclusive: bool
        reason: str
        if ok:
            conclusive = True
            reason = f"matched receipt entry (idx={matched_idx})"
        elif not token_schema_ok_any:
            conclusive = False
            reason = "receipt schema missing request scopes for token verification"
        elif not token_ok_any:
            conclusive = True
            reason = "token not found in receipt request scopes"
        elif mismatches:
            conclusive = True
            reason = "expected fields did not match"
        else:
            conclusive = True
            reason = "no receipt entry matched token + expected fields"

        result_preview: Dict[str, Any] = {
            "path": str(candidate),
            "raw_sha256": raw_sha256,
            "mtime_ms": mtime_ms,
            "within_time_window": within_window,
            "host_window": {
                "start_ms": host_window.start_ms,
                "end_ms": host_window.end_ms,
                "t0_ms": host_window.t0_ms,
                "slack_ms": host_window.slack_ms,
            },
            "entry_count": len(entries),
            "matched_entry_idx": matched_idx,
            "token_match": token_detail,
            "expected_keys": sorted(str(k) for k in self._expected.keys()),
            "matched_fields": matched_fields,
            "mismatches": mismatches,
        }

        artifact: Optional[Dict[str, Any]] = None
        artifact_error: Optional[str] = None
        if self._store_artifact:
            rel_name = (candidate.name or "network_receipt.json").replace("/", "_")
            artifact_rel = Path("oracle_artifacts") / f"network_receipt_post_{rel_name}"
            artifact_obj = {
                "schema": "network_receipt_redacted_v1",
                "source": {
                    "path": str(candidate),
                    "raw_sha256": raw_sha256,
                    "mtime_ms": mtime_ms,
                },
                "host_window": result_preview["host_window"],
                "entry_count": len(entries),
                "matched_entry_idx": matched_idx,
                "token_match": token_detail,
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
                        "Hard oracle: validates a server/host receipt for a network request "
                        "(UI spoof-resistant)."
                    ),
                    (
                        "Token verification: requires the per-episode token to appear in request "
                        "body/header/query scopes (prevents stale/historical passes)."
                    ),
                    (
                        "Time window: receipt file mtime must lie within the episode host window "
                        "(prevents stale/historical false positives)."
                    ),
                    (
                        "Privacy: only stores hashes/summaries in evidence; does not copy raw "
                        "request payloads."
                    ),
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


@register_oracle(NetworkReceiptOracle.oracle_id)
def _make_network_receipt(cfg: Mapping[str, Any]) -> Oracle:
    path = cfg.get("path")
    glob = cfg.get("glob") or cfg.get("pattern")
    expected = cfg.get("expected") or cfg.get("expect") or {}
    token = cfg.get("token")
    token_path = cfg.get("token_path")
    token_match = cfg.get("token_match", "equals")
    token_scopes = cfg.get("token_scopes") or cfg.get("token_scope")
    clear_before_run = bool(cfg.get("clear_before_run", True) or cfg.get("clear_receipts", False))
    store_artifact = bool(cfg.get("store_artifact", True))
    require_token = bool(cfg.get("require_token", True))

    if expected is None:
        expected = {}
    if not isinstance(expected, Mapping):
        raise ValueError("expected/expect must be an object")

    scopes: Optional[Sequence[str]] = None
    if token_scopes is not None:
        if not isinstance(token_scopes, (list, tuple)):
            raise ValueError("token_scopes must be a list of strings")
        scopes = [str(s) for s in token_scopes if str(s).strip()]

    return NetworkReceiptOracle(
        path=str(path) if path is not None else None,
        glob=str(glob) if glob is not None else None,
        expected=dict(expected),
        token=str(token) if token is not None else None,
        token_path=str(token_path) if token_path is not None else None,
        token_match=str(token_match),
        token_scopes=scopes,
        clear_before_run=clear_before_run,
        store_artifact=store_artifact,
        require_token=require_token,
    )


@register_oracle("NetworkReceiptOracle")
def _make_network_receipt_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_network_receipt(cfg)
