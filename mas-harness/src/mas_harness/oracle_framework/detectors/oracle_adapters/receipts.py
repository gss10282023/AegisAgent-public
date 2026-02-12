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


def _canonical_artifacts(event: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = event.get("artifacts")
    out: list[dict[str, Any]] = []
    if not isinstance(artifacts, list):
        return out
    for a in artifacts:
        if not isinstance(a, dict):
            continue
        path = _nonempty_str(a.get("path"))
        if path is None:
            continue
        out.append(
            {
                "path": path,
                "sha256": _nonempty_str(a.get("sha256")),
                "bytes": int(a.get("bytes")) if isinstance(a.get("bytes"), int) else None,
                "mime": _nonempty_str(a.get("mime")),
            }
        )
    out.sort(key=lambda d: str(d.get("path") or ""))
    return out


@register_adapter("HostArtifactReceiptAdapter", priority=70)
class HostArtifactReceiptAdapter:
    adapter_name = "HostArtifactReceiptAdapter"
    priority = 70

    def matches(self, event: dict[str, Any]) -> bool:
        name = _nonempty_str(event.get("oracle_name")) or ""
        if name in {
            "host_artifact_json",
            "sdcard_json_receipt",
            "clipboard_receipt",
            "notification_listener_receipt",
        }:
            return True
        if name.endswith("_receipt"):
            return True
        return False

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        artifacts = _canonical_artifacts(event)
        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.receipt.host_artifact_summary/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]


@register_adapter("NetworkReceiptAdapter", priority=75)
class NetworkReceiptAdapter:
    adapter_name = "NetworkReceiptAdapter"
    priority = 75

    def matches(self, event: dict[str, Any]) -> bool:
        return _nonempty_str(event.get("oracle_name")) == "network_receipt"

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        preview = event.get("result_preview")
        entry_count = None
        matched_entry_idx = None
        expected_keys: list[str] = []
        if isinstance(preview, dict):
            if isinstance(preview.get("entry_count"), int):
                entry_count = int(preview.get("entry_count"))
            if isinstance(preview.get("matched_entry_idx"), int):
                matched_entry_idx = int(preview.get("matched_entry_idx"))
            exp = preview.get("expected_keys")
            if isinstance(exp, list):
                expected_keys = sorted({str(k) for k in exp if str(k).strip()})

        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "entry_count": entry_count,
            "matched_entry_idx": matched_entry_idx,
            "expected_keys": expected_keys,
            "preview_hash": stable_sha256(preview) if preview is not None else None,
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.receipt.network_summary/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]
