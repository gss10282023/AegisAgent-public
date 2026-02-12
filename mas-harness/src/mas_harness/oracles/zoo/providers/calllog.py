"""CallLog ContentProvider oracles.

Phase 2 Step 8 implementation: validate calls by querying `content://call_log/calls`
via adb content providers.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

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


def _normalize_phone(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _phone_matches(candidate: str, expected: str) -> bool:
    cand = _normalize_phone(candidate)
    exp = _normalize_phone(expected)
    if not cand or not exp:
        return False
    return cand == exp or cand.endswith(exp) or exp.endswith(cand)


_CALL_TYPE_MAP = {
    "incoming": "1",
    "in": "1",
    "outgoing": "2",
    "out": "2",
    "missed": "3",
    "voicemail": "4",
    "rejected": "5",
    "blocked": "6",
    "answered_externally": "7",
}


def _normalize_call_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s.isdigit():
        return s
    return _CALL_TYPE_MAP.get(s)


class CallLogProviderOracle(Oracle):
    oracle_id = "calllog_provider"
    oracle_name = "calllog_provider"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        phone_number: str,
        call_type: Optional[str] = None,
        timeout_ms: int = 15_000,
        limit: int = 50,
    ) -> None:
        self._phone_number = str(phone_number)
        self._call_type = _normalize_call_type(call_type)
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
                            cmd="shell content query --uri content://call_log/calls",
                            uri="content://call_log/calls",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: queries CallLog provider via adb; robust to UI spoofing.",
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
                            cmd="shell content query --uri content://call_log/calls",
                            uri="content://call_log/calls",
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

        uri = "content://call_log/calls"
        projection = "_id:number:date:type:duration"
        where = f"date >= {int(window.start_ms)}"
        sort = "date DESC"
        cmd = _content_query_cmd(
            uri=uri,
            projection=projection,
            where=where,
            sort=sort,
            limit=self._limit,
        )

        meta = _run_content_query(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        ok = _content_query_meta_ok(meta)
        stdout = str(meta.get("stdout", "") or "")
        rows = parse_content_query_output(
            stdout,
            expected_keys=("_id", "number", "date", "type", "duration"),
        )

        matches: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            call_id = str(row.get("_id", "") or "")
            if call_id and call_id in seen:
                continue
            number = str(row.get("number", "") or "")
            if not _phone_matches(number, self._phone_number):
                continue
            raw_date = row.get("date")
            date_ms = parse_epoch_time_ms(str(raw_date)) if raw_date is not None else None
            if date_ms is None or not window.contains(date_ms):
                continue
            if self._call_type is not None:
                if str(row.get("type", "") or "").strip() != self._call_type:
                    continue
            if call_id:
                seen.add(call_id)
            matches.append(
                {
                    "_id": call_id or None,
                    "number": number,
                    "date_ms": date_ms,
                    "type": str(row.get("type", "") or ""),
                }
            )

        matched = bool(matches)
        if matched:
            conclusive = True
            success = True
            reason = f"matched {len(matches)} call log row(s)"
        elif ok:
            conclusive = True
            success = False
            reason = "no matching call log rows found"
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
                        where=where,
                        sort=sort,
                        limit=self._limit,
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "phone_number": self._phone_number,
                    "call_type": self._call_type,
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
                        "Hard oracle: validates call log via adb content providers "
                        "(UI spoof-resistant)."
                    ),
                    "Anti-gaming: requires phone number + time window match.",
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


@register_oracle(CallLogProviderOracle.oracle_id)
def _make_calllog_provider(cfg: Mapping[str, Any]) -> Oracle:
    number = cfg.get("phone_number") or cfg.get("number")
    if not isinstance(number, str) or not number:
        raise ValueError("CallLogProviderOracle requires 'phone_number' string")

    call_type = cfg.get("call_type")
    if call_type is not None and not isinstance(call_type, str):
        call_type = str(call_type)

    return CallLogProviderOracle(
        phone_number=number,
        call_type=call_type,
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
        limit=int(cfg.get("limit", 50)),
    )


@register_oracle("CallLogProviderOracle")
def _make_calllog_provider_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_calllog_provider(cfg)
