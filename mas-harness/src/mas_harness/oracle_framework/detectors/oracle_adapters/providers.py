from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mas_harness.evidence import stable_sha256
from mas_harness.oracle_framework.detectors.oracle_adapters.registry import register_adapter
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _hash_prefix(value: Any, *, n: int = 12) -> str:
    return stable_sha256(str(value or ""))[: int(n)]


@register_adapter("SmsProviderAdapter", priority=100)
class SmsProviderAdapter:
    adapter_name = "SmsProviderAdapter"
    priority = 100

    def matches(self, event: dict[str, Any]) -> bool:
        return _nonempty_str(event.get("oracle_name")) == "sms_provider"

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        preview = event.get("result_preview")
        matched = None
        match_count = None
        box = None
        recipients_hashes: list[str] = []
        message_hashes: list[str] = []

        if isinstance(preview, dict):
            if isinstance(preview.get("matched"), bool):
                matched = bool(preview.get("matched"))
            if isinstance(preview.get("match_count"), int):
                match_count = int(preview.get("match_count"))
            box = _nonempty_str(preview.get("box"))

            matches = preview.get("matches")
            if isinstance(matches, list):
                for m in matches:
                    if not isinstance(m, dict):
                        continue
                    addr = _nonempty_str(m.get("address"))
                    body_preview = _nonempty_str(m.get("body_preview"))
                    if addr is not None:
                        recipients_hashes.append(_hash_prefix(addr))
                    if body_preview is not None:
                        message_hashes.append(_hash_prefix(body_preview))

        recipients_hashes = sorted(set(recipients_hashes))
        message_hashes = sorted(set(message_hashes))

        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "matched": matched,
            "match_count": match_count,
            "box": box,
            "recipients_hashes": recipients_hashes,
            "message_body_hashes": message_hashes,
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.provider.sms_activity_summary/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]


@register_adapter("ContactsProviderAdapter", priority=90)
class ContactsProviderAdapter:
    adapter_name = "ContactsProviderAdapter"
    priority = 90

    def matches(self, event: dict[str, Any]) -> bool:
        return _nonempty_str(event.get("oracle_name")) == "contacts_provider"

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        preview = event.get("result_preview")
        matched = None
        match_count = None
        if isinstance(preview, dict):
            if isinstance(preview.get("matched"), bool):
                matched = bool(preview.get("matched"))
            if isinstance(preview.get("match_count"), int):
                match_count = int(preview.get("match_count"))

        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "matched": matched,
            "match_count": match_count,
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.provider.contacts_activity_summary/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]


@register_adapter("CalendarProviderAdapter", priority=90)
class CalendarProviderAdapter:
    adapter_name = "CalendarProviderAdapter"
    priority = 90

    def matches(self, event: dict[str, Any]) -> bool:
        return _nonempty_str(event.get("oracle_name")) == "calendar_provider"

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        preview = event.get("result_preview")
        matched = None
        match_count = None
        if isinstance(preview, dict):
            if isinstance(preview.get("matched"), bool):
                matched = bool(preview.get("matched"))
            if isinstance(preview.get("match_count"), int):
                match_count = int(preview.get("match_count"))

        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "matched": matched,
            "match_count": match_count,
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.provider.calendar_activity_summary/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]
