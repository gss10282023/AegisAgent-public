"""Contacts ContentProvider oracles.

Phase 2 Step 8 implementation: validate contact creation by querying the
Contacts content providers via adb.
"""

from __future__ import annotations

import re
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
    content_query_meta_ok as _content_query_meta_ok,
)
from mas_harness.oracles.zoo.utils.adb_content import (
    run_content_query as _run_content_query,
)
from mas_harness.oracles.zoo.utils.adb_parsing import parse_content_query_output
from mas_harness.oracles.zoo.utils.time_window import parse_epoch_time_ms


def _normalize_phone(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _phone_match(candidate: str, expected: str, *, mode: str) -> bool:
    cand = _normalize_phone(candidate)
    exp = _normalize_phone(expected)
    if not cand or not exp:
        return False
    if mode == "exact":
        return cand == exp
    if mode == "endswith":
        return cand.endswith(exp) or exp.endswith(cand)
    raise ValueError("ContactsProviderOracle.phone_match must be: exact|endswith")


def _find_contact_candidates(
    phone_rows: Sequence[Mapping[str, Any]],
    *,
    name: str,
    phone_number: str,
    phone_match: str,
    token: Optional[str],
) -> List[Tuple[str, Dict[str, Any]]]:
    expected_name = name.strip().lower()
    candidates: List[Tuple[str, Dict[str, Any]]] = []
    seen: set[str] = set()

    for row in phone_rows:
        contact_id = str(row.get("contact_id", "") or row.get("CONTACT_ID", "") or "")
        if not contact_id or contact_id in seen:
            continue

        display = str(row.get("display_name", "") or "")
        number = str(row.get("number", "") or "")

        if expected_name and expected_name not in display.lower():
            continue
        if token and token not in display:
            continue
        if not _phone_match(number, phone_number, mode=phone_match):
            continue

        seen.add(contact_id)
        candidates.append(
            (
                contact_id,
                {
                    "contact_id": contact_id,
                    "display_name": display,
                    "number": number,
                },
            )
        )

    return candidates


class ContactsProviderOracle(Oracle):
    oracle_id = "contacts_provider"
    oracle_name = "contacts_provider"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        name: str,
        phone_number: str,
        token: Optional[str] = None,
        phone_match: str = "endswith",
        timeout_ms: int = 15_000,
        limit: int = 400,
    ) -> None:
        self._name = str(name)
        self._phone_number = str(phone_number)
        self._token = str(token) if token else None
        self._phone_match = str(phone_match)
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
                            cmd="shell content query --uri content://contacts/phones/",
                            uri="content://contacts/phones/",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: queries Contacts provider via adb; robust to UI spoofing.",
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
                            cmd="shell content query --uri content://contacts/phones/",
                            uri="content://contacts/phones/",
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

        queries: List[Dict[str, Any]] = []

        # Step 1: find candidate contacts by name + phone.
        phones_uri = "content://contacts/phones/"
        phones_projection = "contact_id:display_name:number"
        phones_cmd = _content_query_cmd(
            uri=phones_uri,
            projection=phones_projection,
            sort="contact_id DESC",
            limit=self._limit,
        )
        phones_meta = _run_content_query(controller, cmd=phones_cmd, timeout_ms=self._timeout_ms)
        phones_ok = _content_query_meta_ok(phones_meta)
        phones_stdout = str(phones_meta.get("stdout", "") or "")
        phone_rows = parse_content_query_output(
            phones_stdout, expected_keys=("contact_id", "display_name", "number")
        )

        queries.append(
            make_query(
                query_type="content_query",
                cmd=f"shell {phones_cmd}",
                uri=phones_uri,
                projection=phones_projection,
                sort="contact_id DESC",
                limit=self._limit,
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
            )
        )

        candidates = _find_contact_candidates(
            phone_rows,
            name=self._name,
            phone_number=self._phone_number,
            phone_match=self._phone_match,
            token=self._token,
        )

        if not candidates:
            conclusive = bool(phones_ok)
            reason = "no matching contact found" if phones_ok else "content query failed"
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
                        "phones_query": phones_meta,
                        "candidates": [],
                    },
                    result_preview={"matched": False, "candidate_count": 0},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: validates contact creation via adb content providers "
                            "(UI spoof-resistant)."
                        ),
                        "Anti-gaming: requires name + phone match.",
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

        # Step 2: enforce time window using contact_last_updated_timestamp (best-effort).
        contacts_uri = "content://com.android.contacts/contacts"
        contacts_projection = "_id:contact_last_updated_timestamp"

        matches: List[Dict[str, Any]] = []
        time_unknown: List[Dict[str, Any]] = []
        any_contact_query_failed = False

        for contact_id, snapshot in candidates:
            where = f"_id={int(contact_id)}"
            contacts_cmd = _content_query_cmd(
                uri=contacts_uri,
                projection=contacts_projection,
                where=where,
                limit=1,
            )
            meta = _run_content_query(controller, cmd=contacts_cmd, timeout_ms=self._timeout_ms)
            ok = _content_query_meta_ok(meta)
            any_contact_query_failed = any_contact_query_failed or (not ok)

            stdout = str(meta.get("stdout", "") or "")
            rows = parse_content_query_output(
                stdout, expected_keys=("_id", "contact_last_updated_timestamp")
            )

            queries.append(
                make_query(
                    query_type="content_query",
                    cmd=f"shell {contacts_cmd}",
                    uri=contacts_uri,
                    projection=contacts_projection,
                    where=where,
                    limit=1,
                    timeout_ms=self._timeout_ms,
                    serial=ctx.serial,
                )
            )

            ts_ms: Optional[int] = None
            if rows:
                raw = rows[0].get("contact_last_updated_timestamp")
                if raw is not None:
                    ts_ms = parse_epoch_time_ms(str(raw))

            if ts_ms is None:
                time_unknown.append({"candidate": snapshot, "meta": meta, "rows": rows})
                continue

            if window.contains(ts_ms):
                matches.append({"candidate": snapshot, "last_updated_ms": ts_ms})

        matched = bool(matches)
        if matched:
            conclusive = True
            success = True
            reason = f"matched {len(matches)} contact(s) within time window"
        elif time_unknown:
            conclusive = False
            success = False
            reason = "contact match found but cannot verify last_updated timestamp"
        elif any_contact_query_failed or (not phones_ok):
            conclusive = False
            success = False
            reason = "content query failed (cannot conclude absence)"
        else:
            conclusive = True
            success = False
            reason = "contact match found but outside time window"

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
                    "name": self._name,
                    "phone_number": self._phone_number,
                    "token": self._token,
                    "phone_match": self._phone_match,
                    "phones_query": phones_meta,
                    "candidates": candidates,
                    "matches": matches,
                    "time_unknown": time_unknown,
                },
                result_preview={
                    "matched": matched,
                    "match_count": len(matches),
                    "matches": matches[:3],
                    "time_unknown_count": len(time_unknown),
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: validates contacts via adb content providers "
                        "(UI spoof-resistant)."
                    ),
                    "Anti-gaming: requires name + phone match and verifies last_updated timestamp.",
                    "Dedup: uses contact_id to avoid counting duplicates.",
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


@register_oracle(ContactsProviderOracle.oracle_id)
def _make_contacts_provider(cfg: Mapping[str, Any]) -> Oracle:
    name = cfg.get("name") or cfg.get("display_name")
    phone = cfg.get("phone_number") or cfg.get("number")
    if not isinstance(name, str) or not name:
        raise ValueError("ContactsProviderOracle requires 'name' string")
    if not isinstance(phone, str) or not phone:
        raise ValueError("ContactsProviderOracle requires 'phone_number' string")

    token = cfg.get("token")
    if token is not None and not isinstance(token, str):
        token = str(token)

    return ContactsProviderOracle(
        name=name,
        phone_number=phone,
        token=token,
        phone_match=str(cfg.get("phone_match", "endswith")),
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
        limit=int(cfg.get("limit", 400)),
    )


@register_oracle("ContactsProviderOracle")
def _make_contacts_provider_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_contacts_provider(cfg)
