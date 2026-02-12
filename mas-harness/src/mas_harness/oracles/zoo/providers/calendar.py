"""Calendar ContentProvider oracles.

Phase 2 Step 8 implementation: validate calendar event creation by querying the
Calendar provider via adb `content query`.

Note on time window
-------------------
Calendar events frequently have a `dtstart` far in the future (e.g. "next week"),
so filtering by `dtstart` is not a reliable *creation-time* signal. To prevent
stale/historical false positives we instead use a pre-run baseline `_id` and
require post-run matches to have `_id > baseline_max_id`.
"""

from __future__ import annotations

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


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except Exception:
        return None


class CalendarProviderOracle(Oracle):
    oracle_id = "calendar_provider"
    oracle_name = "calendar_provider"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        token: str,
        fields: Sequence[str] = ("title", "description"),
        timeout_ms: int = 15_000,
        limit: int = 200,
    ) -> None:
        self._token = str(token)
        self._fields = tuple(str(f) for f in fields)
        self._timeout_ms = int(timeout_ms)
        self._limit = int(limit)
        self._baseline_max_id: Optional[int] = None

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller
        if not hasattr(controller, "adb_shell"):
            return []

        uri = "content://com.android.calendar/events"
        projection = "_id"
        cmd = _content_query_cmd(uri=uri, projection=projection, sort="_id DESC", limit=1)
        meta = _run_content_query(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        ok = _content_query_meta_ok(meta)
        stdout = str(meta.get("stdout", "") or "")
        rows = parse_content_query_output(stdout, expected_keys=("_id",))
        baseline: Optional[int] = None
        if rows:
            baseline = _safe_int(rows[0].get("_id"))
        self._baseline_max_id = baseline

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="pre",
                queries=[
                    make_query(
                        query_type="content_query",
                        cmd=f"shell {cmd}",
                        uri=uri,
                        projection=projection,
                        sort="_id DESC",
                        limit=1,
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={
                    "ok": ok,
                    "baseline_max_id": baseline,
                    "meta": meta,
                    "rows": rows,
                },
                result_preview={"ok": ok, "baseline_max_id": baseline},
                anti_gaming_notes=[
                    (
                        "Pollution control: captures pre-run max calendar event _id so post-run "
                        "validation can require a strictly newer record."
                    ),
                ],
                decision=make_decision(
                    success=bool(ok),
                    score=1.0 if ok else 0.0,
                    reason="captured baseline max _id" if ok else "failed to query calendar events",
                    conclusive=bool(ok),
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]

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
                            cmd="shell content query --uri content://com.android.calendar/events",
                            uri="content://com.android.calendar/events",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: queries Calendar provider via adb; robust to UI spoofing.",
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
                            cmd="shell content query --uri content://com.android.calendar/events",
                            uri="content://com.android.calendar/events",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: requires an episode time anchor to prevent stale "
                            "passes (uses a baseline `_id` captured at episode start)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing episode time anchor",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["episode_time_anchor"],
                )
            ]

        uri = "content://com.android.calendar/events"
        projection = "_id:title:description:dtstart:dtend"

        baseline = self._baseline_max_id
        where = f"_id > {baseline}" if isinstance(baseline, int) else None

        cmd = _content_query_cmd(
            uri=uri,
            projection=projection,
            where=where,
            sort="_id DESC",
            limit=self._limit,
        )
        meta = _run_content_query(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        ok = _content_query_meta_ok(meta)
        stdout = str(meta.get("stdout", "") or "")
        rows = parse_content_query_output(
            stdout, expected_keys=("_id", "title", "description", "dtstart", "dtend")
        )

        matches: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            event_id = str(row.get("_id", "") or "")
            if not event_id or event_id in seen:
                continue

            if isinstance(baseline, int):
                eid_int = _safe_int(event_id)
                if eid_int is None or eid_int <= baseline:
                    continue

            for field in self._fields:
                val = str(row.get(field, "") or "")
                if self._token and self._token in val:
                    seen.add(event_id)
                    matches.append(
                        {
                            "_id": event_id,
                            "title_preview": str(row.get("title", "") or "")[:120],
                            "dtstart": row.get("dtstart"),
                            "dtend": row.get("dtend"),
                        }
                    )
                    break

        matched = bool(matches)
        if matched:
            conclusive = True
            success = True
            reason = f"matched {len(matches)} event(s) with token"
        elif ok and isinstance(baseline, int):
            conclusive = True
            success = False
            reason = "no matching calendar events found"
        else:
            conclusive = False
            success = False
            reason = "calendar query failed or baseline missing"

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
                        where=where,
                        sort="_id DESC",
                        limit=self._limit,
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={
                    "baseline_max_id": baseline,
                    "token": self._token,
                    "fields": list(self._fields),
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
                        "Hard oracle: validates calendar events via adb content providers "
                        "(UI spoof-resistant)."
                    ),
                    "Anti-gaming: requires token in event title/description.",
                    "Pollution control: requires _id > pre-run baseline max _id.",
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


@register_oracle(CalendarProviderOracle.oracle_id)
def _make_calendar_provider(cfg: Mapping[str, Any]) -> Oracle:
    token = cfg.get("token") or cfg.get("title_token")
    if not isinstance(token, str) or not token:
        raise ValueError("CalendarProviderOracle requires 'token' string")

    fields = cfg.get("fields", ("title", "description"))
    if isinstance(fields, str):
        fields = [fields]
    elif not isinstance(fields, (list, tuple)):
        fields = ["title", "description"]

    return CalendarProviderOracle(
        token=token,
        fields=[str(f) for f in fields],
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
        limit=int(cfg.get("limit", 200)),
    )


@register_oracle("CalendarProviderOracle")
def _make_calendar_provider_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_calendar_provider(cfg)
