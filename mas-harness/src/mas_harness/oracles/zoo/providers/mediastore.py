"""MediaStore ContentProvider oracles.

Phase 2 Step 8 implementation: validate media creation by querying MediaStore via
adb `content query`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

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
    content_query_meta_ok as _content_query_meta_ok,
)
from mas_harness.oracles.zoo.utils.adb_content import (
    run_content_query as _run_content_query,
)
from mas_harness.oracles.zoo.utils.adb_parsing import parse_content_query_output
from mas_harness.oracles.zoo.utils.time_window import parse_epoch_time_ms

_COLLECTION_URIS = {
    "images": "content://media/external/images/media",
    "image": "content://media/external/images/media",
    "videos": "content://media/external/video/media",
    "video": "content://media/external/video/media",
    "audio": "content://media/external/audio/media",
    "files": "content://media/external/file",
    "file": "content://media/external/file",
}


class MediaStoreOracle(Oracle):
    oracle_id = "mediastore"
    oracle_name = "mediastore"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        token: str,
        collection: str = "images",
        timeout_ms: int = 15_000,
        limit: int = 200,
    ) -> None:
        self._token = str(token)
        self._collection = str(collection)
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
                            cmd="shell content query --uri content://media/external/...",
                            uri="content://media/external/...",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: queries MediaStore via adb; robust to UI spoofing.",
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
                            cmd="shell content query --uri content://media/external/...",
                            uri="content://media/external/...",
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
                        "Hard oracle: needs device epoch time to enforce a time window.",
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

        collection = self._collection.strip().lower()
        uri = _COLLECTION_URIS.get(collection)
        if uri is None:
            raise ValueError(
                "MediaStoreOracle.collection must be one of: images|videos|audio|files"
            )

        projection = "_id:_display_name:date_added:date_modified:relative_path:_data"
        sort = "date_added DESC"
        cmd = _content_query_cmd(uri=uri, projection=projection, sort=sort, limit=self._limit)

        meta = _run_content_query(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        ok = _content_query_meta_ok(meta)
        stdout = str(meta.get("stdout", "") or "")
        rows = parse_content_query_output(
            stdout,
            expected_keys=(
                "_id",
                "_display_name",
                "date_added",
                "date_modified",
                "relative_path",
                "_data",
            ),
        )

        matches: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            media_id = str(row.get("_id", "") or "")
            if media_id and media_id in seen:
                continue

            name = str(row.get("_display_name", "") or row.get("display_name", "") or "")
            if self._token and self._token not in name:
                continue

            ts_raw = row.get("date_modified") or row.get("date_added")
            ts_ms = parse_epoch_time_ms(str(ts_raw)) if ts_raw is not None else None
            if ts_ms is None or not window.contains(ts_ms):
                continue

            if media_id:
                seen.add(media_id)
            matches.append(
                {
                    "_id": media_id or None,
                    "_display_name": name,
                    "time_ms": ts_ms,
                    "relative_path": row.get("relative_path"),
                }
            )

        matched = bool(matches)
        if matched:
            conclusive = True
            success = True
            reason = f"matched {len(matches)} media row(s)"
        elif ok:
            conclusive = True
            success = False
            reason = "no matching media rows found"
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
                queries=[
                    make_query(
                        query_type="content_query",
                        cmd=f"shell {cmd}",
                        uri=uri,
                        projection=projection,
                        sort=sort,
                        limit=self._limit,
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "collection": collection,
                    "token": self._token,
                    "ok": ok,
                    "meta": meta,
                    "rows": rows,
                    "matches": matches,
                },
                result_preview={
                    "matched": matched,
                    "match_count": len(matches),
                    "matches": matches[:3],
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: validates media creation via MediaStore (adb content query); "
                        "robust to UI spoofing."
                    ),
                    "Anti-gaming: requires display_name token + time window match.",
                    "Dedup: uses _id to avoid counting duplicates.",
                ],
                decision=make_decision(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]


@register_oracle(MediaStoreOracle.oracle_id)
def _make_mediastore(cfg: Mapping[str, Any]) -> Oracle:
    token = cfg.get("token") or cfg.get("display_name_token")
    if not isinstance(token, str) or not token:
        raise ValueError("MediaStoreOracle requires 'token' string")

    return MediaStoreOracle(
        token=token,
        collection=str(cfg.get("collection", "images")),
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
        limit=int(cfg.get("limit", 200)),
    )


@register_oracle("MediaStoreOracle")
def _make_mediastore_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_mediastore(cfg)
