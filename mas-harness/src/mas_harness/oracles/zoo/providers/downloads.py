"""DownloadManager (downloads provider) oracle.

Phase 2 Step 1.1 (high ROI): validate downloads by querying the DownloadManager
provider via `adb shell content query`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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
from mas_harness.oracles.zoo.utils.adb_content import (
    content_query_cmd as _content_query_cmd,
)
from mas_harness.oracles.zoo.utils.adb_content import (
    content_query_error_kind as _content_query_error_kind,
)
from mas_harness.oracles.zoo.utils.adb_content import (
    content_query_meta_ok as _content_query_meta_ok,
)
from mas_harness.oracles.zoo.utils.adb_content import (
    run_content_query as _run_content_query,
)
from mas_harness.oracles.zoo.utils.adb_parsing import (
    is_content_query_no_result,
    parse_content_query_output,
)
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256, stable_sha256
from mas_harness.oracles.zoo.utils.time_window import TimeWindow, parse_epoch_time_ms

_DEFAULT_URIS: Tuple[str, ...] = (
    "content://downloads/my_downloads",
    "content://downloads/public_downloads",
)

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_TOKEN_FIELDS: Tuple[str, ...] = (
    "title",
    "description",
    "_display_name",
    "uri",
    "local_uri",
    "local_filename",
    "_data",
    "hint",
    "filename",
    "file_name",
)

_PACKAGE_FIELDS: Tuple[str, ...] = (
    "notificationpackage",
    "notification_package",
    "package",
    "pkg",
    "package_name",
    "caller_package_name",
)

_TIME_FIELDS: Tuple[str, ...] = (
    "lastmod",
    "last_modified_timestamp",
    "last_modified",
    "last_update_time",
    "timestamp",
    "time",
    "date",
)


def _safe_name(text: str, *, default: str) -> str:
    name = str(text or "").strip()
    name = name.rsplit("/", 1)[-1] if "/" in name else name
    name = _SAFE_NAME_RE.sub("_", name)
    return name or default


def _write_text_artifact(
    ctx: OracleContext,
    *,
    rel_path: Path,
    text: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if ctx.episode_dir is None:
        return None, "missing episode_dir (cannot persist artifact)"

    try:
        out_path = ctx.episode_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        sha256 = stable_file_sha256(out_path)
        return (
            {
                "path": rel_path.as_posix(),
                "sha256": sha256,
                "bytes": len(text.encode("utf-8")),
                "mime": "text/plain",
            },
            None,
        )
    except Exception as e:  # pragma: no cover
        return None, f"artifact_write_failed:{type(e).__name__}:{e}"


def _parse_status_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def _is_success_status(value: Any) -> bool:
    """Best-effort success mapping across Android variants.

    DownloadManager's underlying provider commonly stores HTTP-style codes
    (200..299) for success, but some environments may expose 8 for SUCCESSFUL.
    """

    status_int = _parse_status_int(value)
    if status_int is not None:
        if status_int == 8:
            return True
        return 200 <= status_int < 300

    s = str(value or "").strip().lower()
    return bool(s and "success" in s)


def _extract_row_time_ms(row: Mapping[str, Any]) -> Optional[int]:
    for key in _TIME_FIELDS:
        raw = row.get(key)
        if raw is None:
            continue
        parsed = parse_epoch_time_ms(str(raw))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _extract_row_package(row: Mapping[str, Any]) -> Optional[str]:
    for key in _PACKAGE_FIELDS:
        raw = row.get(key)
        if raw is None:
            continue
        val = str(raw).strip()
        if val:
            return val
    return None


def _row_contains_token(row: Mapping[str, Any], *, token: str) -> bool:
    if not token:
        return False

    for key in _TOKEN_FIELDS:
        raw = row.get(key)
        if raw is None:
            continue
        if token in str(raw):
            return True

    # Fallback (best-effort): scan other string fields.
    for raw in row.values():
        if isinstance(raw, str) and token in raw:
            return True
    return False


def _match_download_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    token: str,
    window: TimeWindow,
    expected_package: Optional[str],
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for row in rows:
        download_id = str(row.get("_id", "") or row.get("id", "") or "")
        if download_id and download_id in seen_ids:
            continue

        if not _is_success_status(row.get("status")):
            continue
        if not _row_contains_token(row, token=token):
            continue

        ts_ms = _extract_row_time_ms(row)
        if ts_ms is None or not window.contains(ts_ms):
            continue

        pkg = _extract_row_package(row)
        if expected_package is not None and pkg != expected_package:
            continue

        if download_id:
            seen_ids.add(download_id)

        matches.append(
            {
                "_id": download_id or None,
                "title": row.get("title") or row.get("_display_name"),
                "uri": row.get("uri"),
                "local_uri": row.get("local_uri"),
                "local_filename": row.get("local_filename") or row.get("_data"),
                "status": row.get("status"),
                "time_ms": ts_ms,
                "package": pkg,
            }
        )

    matches.sort(key=lambda m: (int(m["time_ms"]), str(m.get("_id") or "")), reverse=True)
    return matches


class DownloadManagerOracle(Oracle):
    """Validate downloads by querying the DownloadManager provider via adb."""

    oracle_id = "download_manager"
    oracle_name = "download_manager"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        token: str,
        package: Optional[str] = None,
        uris: Sequence[str] | None = None,
        timeout_ms: int = 15_000,
        limit: int = 200,
    ) -> None:
        self._token = str(token)
        self._package = str(package).strip() if package is not None else None
        self._uris = [str(u) for u in (uris or _DEFAULT_URIS) if str(u)]
        self._timeout_ms = int(timeout_ms)
        self._limit = int(limit)

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller

        if not hasattr(controller, "adb_shell"):
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="content_query",
                            cmd="shell content query --uri content://downloads/my_downloads",
                            uri="content://downloads/my_downloads",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: queries DownloadManager provider via adb content query.",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing controller capability: adb_shell",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["adb_shell"],
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
                            query_type="content_query",
                            cmd="shell content query --uri content://downloads/my_downloads",
                            uri="content://downloads/my_downloads",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: requires an episode time anchor to apply a strict "
                            "time window and avoid stale/historical false positives."
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

        window, window_meta = ctx.episode_time.device_window(controller=controller)
        if window is None:
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="adb_cmd",
                            cmd="shell date +%s%3N",
                            timeout_ms=1500,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["device_time_window"], "probe": window_meta},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: requires device epoch time to enforce a strict "
                            "time window for provider queries."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing device_time_window (failed to compute device time window)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["device_time_window"],
                )
            ]

        queries: List[Dict[str, Any]] = []
        per_uri: Dict[str, Any] = {}
        rows_all: List[Dict[str, Any]] = []

        any_ok = False
        any_parse_failed = False

        for uri in self._uris:
            cmd = _content_query_cmd(uri=uri, limit=self._limit)
            meta = _run_content_query(controller, cmd=cmd, timeout_ms=self._timeout_ms)
            ok = _content_query_meta_ok(meta)
            any_ok = any_ok or ok

            stdout = str(meta.get("stdout", "") or "")
            stdout_sha256 = stable_sha256(stdout)
            stdout_len = len(stdout.encode("utf-8"))

            rel_name = _safe_name(uri, default="downloads.txt")
            artifact_rel = Path("oracle") / "raw" / f"content_query_{rel_name}_post.txt"
            artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)

            rows = parse_content_query_output(
                stdout,
                expected_keys=(
                    "_id",
                    "status",
                    "lastmod",
                    "last_modified_timestamp",
                    "title",
                    "description",
                    "uri",
                    "local_uri",
                    "local_filename",
                    "_data",
                    "notificationpackage",
                    "package",
                ),
            )

            no_result = is_content_query_no_result(stdout)
            parse_failed = bool(ok and not no_result and stdout.strip() and not rows)
            any_parse_failed = any_parse_failed or parse_failed

            if ok:
                rows_all.extend(rows)

            per_uri[uri] = {
                "ok": ok,
                "error_kind": _content_query_error_kind(meta),
                "meta": {k: v for k, v in meta.items() if k != "stdout"},
                "stdout_sha256": stdout_sha256,
                "stdout_len": stdout_len,
                "artifact": artifact,
                "artifact_error": artifact_error,
                "row_count": len(rows),
                "no_result": no_result,
                "parse_failed": parse_failed,
            }

            queries.append(
                make_query(
                    query_type="content_query",
                    cmd=f"shell {cmd}",
                    uri=uri,
                    limit=self._limit,
                    timeout_ms=self._timeout_ms,
                    serial=ctx.serial,
                )
            )

        matches = _match_download_rows(
            rows_all,
            token=self._token,
            window=window,
            expected_package=self._package,
        )

        artifacts = [
            info["artifact"] for info in per_uri.values() if info.get("artifact") is not None
        ]
        artifacts_out = artifacts if artifacts else None

        matched = bool(matches)
        if matched:
            conclusive = True
            success = True
            reason = f"matched {len(matches)} download record(s)"
        elif any_parse_failed:
            conclusive = False
            success = False
            reason = "failed to parse downloads provider output"
        elif any_ok:
            conclusive = True
            success = False
            reason = "no matching download records found"
        else:
            conclusive = False
            success = False
            reason = "content query failed (cannot conclude absence)"

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=queries,
                result_for_digest={
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "token": self._token,
                    "expected_package": self._package,
                    "per_uri": per_uri,
                    "matches": matches,
                },
                result_preview={
                    "matched": matched,
                    "match_count": len(matches),
                    "matches": matches[:3],
                    "uris_ok": [u for u, info in per_uri.items() if info.get("ok")],
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: validates downloads by querying DownloadManager provider "
                        "via adb (UI spoof-resistant)."
                    ),
                    (
                        "Anti-gaming: requires status=SUCCESS + token match in title/path/uri "
                        "+ strict device time window match."
                    ),
                    (
                        "Evidence hygiene: stores raw provider output as artifacts and records "
                        "only structured fields + digests in oracle_trace."
                    ),
                ],
                decision=make_decision(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
                artifacts=artifacts_out,
            )
        ]


@register_oracle(DownloadManagerOracle.oracle_id)
def _make_download_manager(cfg: Mapping[str, Any]) -> Oracle:
    token = cfg.get("token") or cfg.get("filename_token") or cfg.get("uri_token")
    if not isinstance(token, str) or not token:
        raise ValueError("DownloadManagerOracle requires 'token' string")

    package = cfg.get("package") or cfg.get("pkg")
    package_str = str(package).strip() if isinstance(package, str) and package.strip() else None

    raw_uris = cfg.get("uris") or cfg.get("uri")
    uris: Sequence[str] | None = None
    if isinstance(raw_uris, str) and raw_uris.strip():
        uris = [raw_uris.strip()]
    elif isinstance(raw_uris, (list, tuple)) and raw_uris:
        uris = [str(u) for u in raw_uris if str(u)]

    return DownloadManagerOracle(
        token=token,
        package=package_str,
        uris=uris,
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
        limit=int(cfg.get("limit", 200)),
    )


@register_oracle("DownloadManagerOracle")
def _make_download_manager_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_download_manager(cfg)
