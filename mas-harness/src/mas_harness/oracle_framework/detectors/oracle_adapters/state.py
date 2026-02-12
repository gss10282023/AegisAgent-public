from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mas_harness.oracle_framework.detectors.oracle_adapters.registry import register_adapter
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


@register_adapter("TelephonyCallStateAdapter", priority=60)
class TelephonyCallStateAdapter:
    adapter_name = "TelephonyCallStateAdapter"
    priority = 60

    def matches(self, event: dict[str, Any]) -> bool:
        return _nonempty_str(event.get("oracle_name")) == "telephony_call_state"

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        preview = event.get("result_preview")
        call_state = None
        call_state_code = None
        expected: list[str] = []
        dumpsys_ok = None
        if isinstance(preview, dict):
            call_state = _nonempty_str(preview.get("call_state"))
            call_state_code = (
                preview.get("call_state_code")
                if isinstance(preview.get("call_state_code"), int)
                else None
            )
            exp = preview.get("expected")
            if isinstance(exp, list):
                expected = sorted({str(x) for x in exp if str(x).strip()})
            if isinstance(preview.get("dumpsys_ok"), bool):
                dumpsys_ok = bool(preview.get("dumpsys_ok"))

        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "call_state": call_state,
            "call_state_code": call_state_code,
            "expected": expected,
            "dumpsys_ok": dumpsys_ok,
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.dumpsys.telephony_call_state/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]


@register_adapter("ResumedActivityAdapter", priority=60)
class ResumedActivityAdapter:
    adapter_name = "ResumedActivityAdapter"
    priority = 60

    def matches(self, event: dict[str, Any]) -> bool:
        return _nonempty_str(event.get("oracle_name")) == "resumed_activity"

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        preview = event.get("result_preview")
        resumed = None
        expected_pkg = None
        matched = None
        if isinstance(preview, dict):
            resumed = _nonempty_str(preview.get("resumed_activity")) or _nonempty_str(
                preview.get("activity")
            )
            expected_pkg = _nonempty_str(preview.get("expected_package")) or _nonempty_str(
                preview.get("package")
            )
            if isinstance(preview.get("matched"), bool):
                matched = bool(preview.get("matched"))

        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "resumed_activity": resumed,
            "expected_package": expected_pkg,
            "matched": matched,
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.system.resumed_activity/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]


@register_adapter("SettingsCheckAdapter", priority=50)
class SettingsCheckAdapter:
    adapter_name = "SettingsCheckAdapter"
    priority = 50

    def matches(self, event: dict[str, Any]) -> bool:
        return _nonempty_str(event.get("oracle_name")) == "settings"

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        preview = event.get("result_preview")
        checks: list[dict[str, Any]] = []
        if isinstance(preview, dict):
            raw_checks = preview.get("checks") or preview.get("results")
            if isinstance(raw_checks, list):
                for c in raw_checks:
                    if not isinstance(c, dict):
                        continue
                    ns = _nonempty_str(c.get("namespace"))
                    key = _nonempty_str(c.get("key"))
                    if ns is None or key is None:
                        continue
                    ok = c.get("ok") if isinstance(c.get("ok"), bool) else None
                    actual = _nonempty_str(c.get("actual")) or _nonempty_str(c.get("value"))
                    expected_any = c.get("expected_any_of") or c.get("expected")
                    expected: list[str] = []
                    if isinstance(expected_any, list):
                        expected = sorted({str(v) for v in expected_any if str(v).strip()})
                    elif expected_any is not None:
                        expected = [str(expected_any)]
                    checks.append(
                        {
                            "namespace": ns,
                            "key": key,
                            "ok": ok,
                            "actual": actual,
                            "expected_any_of": expected,
                        }
                    )

        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "checks": sorted(checks, key=lambda d: (d.get("namespace") or "", d.get("key") or "")),
            "check_count": len(checks),
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.settings.snapshot_summary/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]
