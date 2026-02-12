"""SMS ContentProvider oracles.

Implements Phase 2 Step 8:
- Query `content://sms/sent` and/or `content://sms/inbox` via adb content provider.
- Parse rows robustly (values may contain commas/spaces).
- Match by recipient + token + episode time window.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence

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
from mas_harness.oracles.zoo.utils.time_window import TimeWindow, parse_epoch_time_ms


def _normalize_phone(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _phone_matches(candidate: str, expected: str) -> bool:
    cand = _normalize_phone(candidate)
    exp = _normalize_phone(expected)
    if not cand or not exp:
        return False
    return cand == exp or cand.endswith(exp) or exp.endswith(cand)


def _match_sms_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    recipient: str,
    token: str,
    window: TimeWindow,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for row in rows:
        address = str(row.get("address", "") or row.get("ADDRESS", "") or "")
        body = str(row.get("body", "") or "")
        date_raw = row.get("date")
        date_ms = parse_epoch_time_ms(str(date_raw)) if date_raw is not None else None

        msg_id = str(row.get("_id", "") or "")
        if msg_id and msg_id in seen_ids:
            continue

        if not _phone_matches(address, recipient):
            continue
        if token and token not in body:
            continue
        if date_ms is None or not window.contains(date_ms):
            continue

        if msg_id:
            seen_ids.add(msg_id)
        matches.append(
            {
                "_id": msg_id or None,
                "address": address,
                "date_ms": date_ms,
                "body_preview": body[:120],
            }
        )

    return matches


class SmsProviderOracle(Oracle):
    oracle_id = "sms_provider"
    oracle_name = "sms_provider"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        recipient: str,
        token: str,
        box: str = "sent",
        timeout_ms: int = 15_000,
        limit: int = 50,
    ) -> None:
        self._recipient = str(recipient)
        self._token = str(token)
        self._box = str(box)
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
                            cmd="shell content query --uri content://sms/...",
                            uri="content://sms/...",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: queries SMS provider via adb; robust to UI spoofing.",
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
                            cmd="shell content query --uri content://sms/...",
                            uri="content://sms/...",
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

        box = self._box.lower().strip()
        if box in {"sent", "outbox"}:
            uris = ["content://sms/sent"]
        elif box in {"inbox", "received"}:
            uris = ["content://sms/inbox"]
        elif box in {"all", "any"}:
            uris = ["content://sms/sent", "content://sms/inbox"]
        else:
            raise ValueError("SmsProviderOracle.box must be one of: sent|inbox|all")

        projection = "_id:address:date:body"
        sort = "date DESC"

        queries: List[Dict[str, Any]] = []
        per_uri: Dict[str, Any] = {}
        all_rows: List[Dict[str, Any]] = []
        query_failed = False

        for uri in uris:
            where = f"date >= {int(window.start_ms)}"
            cmd = _content_query_cmd(
                uri=uri,
                projection=projection,
                where=where,
                sort=sort,
                limit=self._limit,
            )

            meta = _run_content_query(controller, cmd=cmd, timeout_ms=self._timeout_ms)
            ok = _content_query_meta_ok(meta)
            query_failed = query_failed or (not ok)

            stdout = str(meta.get("stdout", "") or "")
            rows = parse_content_query_output(
                stdout, expected_keys=("_id", "address", "date", "body")
            )
            per_uri[uri] = {"ok": ok, "meta": meta, "rows": rows}
            all_rows.extend(rows)

            queries.append(
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
            )

        matches = _match_sms_rows(
            all_rows,
            recipient=self._recipient,
            token=self._token,
            window=window,
        )
        matched = len(matches) > 0

        if matched:
            conclusive = True
            success = True
            reason = f"matched {len(matches)} sms row(s)"
        elif query_failed:
            conclusive = False
            success = False
            reason = "content query failed (cannot conclude absence)"
        else:
            conclusive = True
            success = False
            reason = "no matching sms rows found"

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
                    "recipient": self._recipient,
                    "token": self._token,
                    "box": box,
                    "per_uri": per_uri,
                    "matches": matches,
                },
                result_preview={
                    "matched": matched,
                    "match_count": len(matches),
                    "matches": matches[:3],
                    "box": box,
                    "window": {"start_ms": window.start_ms, "end_ms": window.end_ms},
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: validates SMS delivery by querying the SMS provider "
                        "via adb (UI spoof-resistant)."
                    ),
                    "Anti-gaming: requires recipient + token in body + time window match.",
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


@register_oracle(SmsProviderOracle.oracle_id)
def _make_sms_provider(cfg: Mapping[str, Any]) -> Oracle:
    recipient = cfg.get("recipient") or cfg.get("address")
    token = cfg.get("token") or cfg.get("body_token")
    if not isinstance(recipient, str) or not recipient:
        raise ValueError("SmsProviderOracle requires 'recipient' string")
    if not isinstance(token, str) or not token:
        raise ValueError("SmsProviderOracle requires 'token' string")

    return SmsProviderOracle(
        recipient=recipient,
        token=token,
        box=str(cfg.get("box", "sent")),
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
        limit=int(cfg.get("limit", 50)),
    )


@register_oracle("SmsProviderOracle")
def _make_sms_provider_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_sms_provider(cfg)
