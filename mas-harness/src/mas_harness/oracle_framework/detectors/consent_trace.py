from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from mas_harness.evidence import stable_sha256
from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.detectors._jsonl import iter_jsonl_objects
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _safe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None
    return None


def _looks_like_sha256(s: str) -> bool:
    if len(s) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in s.lower())


def _decision_from_any(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return "approved" if value else "declined"
    s = _nonempty_str(value)
    if s is None:
        return None
    norm = s.strip().lower()
    if norm in {"approved", "approve", "ok", "yes", "true", "allow", "allowed"}:
        return "approved"
    if norm in {"declined", "decline", "no", "false", "deny", "denied"}:
        return "declined"
    return None


def _canonical_sink_type(value: Any) -> Optional[str]:
    s = _nonempty_str(value)
    if s is None:
        return None
    norm = s.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "install": "install_package",
        "package_install": "install_package",
        "install_package": "install_package",
        "settings": "settings_change",
        "settings_change": "settings_change",
        "sms": "send_sms",
        "send_sms": "send_sms",
        "calendar": "create_calendar_event",
        "create_calendar_event": "create_calendar_event",
        "contacts": "modify_contacts",
        "modify_contacts": "modify_contacts",
    }
    return aliases.get(norm, norm) or None


def _hash_prefix(value: Any, *, n: int = 12) -> str:
    return stable_sha256(str(value or ""))[: int(n)]


def _canonical_binding_snapshot(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    out: dict[str, Any] = {}
    for key in ("recipient", "app", "origin", "amount"):
        if key not in raw:
            continue
        val = raw.get(key)
        out[f"{key}_hash_prefix"] = _hash_prefix(val)
    return out or None


def _try_parse_consent_events_from_jsonl(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    events: list[dict[str, Any]] = []
    refs: set[str] = {path.name}
    try:
        iterator = iter_jsonl_objects(path)
    except Exception:
        return [], set()

    for line_no, obj in iterator:
        step_idx = _safe_int(
            obj.get("step_idx") if "step_idx" in obj else obj.get("step", obj.get("step_index"))
        )
        sink_type = _canonical_sink_type(
            obj.get("sink_type") if "sink_type" in obj else obj.get("sink", obj.get("action"))
        )
        decision = _decision_from_any(
            obj.get("decision") if "decision" in obj else obj.get("approved")
        )
        if sink_type is None or decision is None:
            continue

        token_raw = (
            obj.get("consent_token_hash")
            if "consent_token_hash" in obj
            else obj.get("consent_token")
        )
        token_str = _nonempty_str(token_raw)
        token_hash = None
        if token_str is not None:
            token_hash = (
                token_str.lower()
                if _looks_like_sha256(token_str.lower())
                else stable_sha256(token_str)
            )

        binding_snapshot = _canonical_binding_snapshot(obj.get("binding_snapshot"))

        line_ref = f"{path.name}:L{int(line_no)}"
        refs.add(line_ref)
        events.append(
            {
                "step_idx": int(step_idx) if step_idx is not None else None,
                "sink_type": sink_type,
                "decision": decision,
                "consent_token_hash": token_hash,
                "binding_snapshot": binding_snapshot,
                "evidence_refs": [line_ref],
            }
        )

    # Stable: sort by (step_idx, sink_type, decision, token_hash_prefix)
    def _sort_key(e: Mapping[str, Any]) -> tuple[int, str, str, str]:
        step = e.get("step_idx")
        step_i = int(step) if isinstance(step, int) else -1
        sink = _nonempty_str(e.get("sink_type")) or ""
        decision_s = _nonempty_str(e.get("decision")) or ""
        token = _nonempty_str(e.get("consent_token_hash")) or ""
        return step_i, sink, decision_s, token[:12]

    events.sort(key=_sort_key)
    return events, refs


def _iter_action_objects(obj: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for key in ("normalized_action", "action", "raw_action"):
        candidate = obj.get(key)
        if isinstance(candidate, Mapping):
            out.append(candidate)
    return out


def _try_parse_consent_events_from_agent_action_trace(
    path: Path,
) -> tuple[list[dict[str, Any]], set[str]]:
    events: list[dict[str, Any]] = []
    refs: set[str] = {path.name}

    try:
        iterator = iter_jsonl_objects(path)
    except Exception:
        return [], set()

    for line_no, obj in iterator:
        step_idx = _safe_int(obj.get("step_idx") if "step_idx" in obj else obj.get("step"))
        action_objs = _iter_action_objects(obj)
        for action in action_objs:
            action_type = _nonempty_str(
                action.get("type") if "type" in action else action.get("action_type")
            )
            action_blob = json.dumps(
                action, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            looks_like_consent = False
            if action_type is not None and any(
                x in action_type.lower() for x in ("confirm", "consent", "handshake")
            ):
                looks_like_consent = True
            if not looks_like_consent and any(
                x in action_blob.lower() for x in ("consent", "confirm", "handshake")
            ):
                looks_like_consent = True
            if not looks_like_consent:
                continue

            sink_type = _canonical_sink_type(
                action.get("sink_type")
                if "sink_type" in action
                else action.get("sink", action.get("capability"))
            )
            decision = _decision_from_any(
                action.get("decision") if "decision" in action else action.get("approved")
            )
            if sink_type is None or decision is None:
                continue

            token_raw = (
                action.get("consent_token_hash")
                if "consent_token_hash" in action
                else action.get("consent_token")
            )
            token_str = _nonempty_str(token_raw)
            token_hash = None
            if token_str is not None:
                token_hash = (
                    token_str.lower()
                    if _looks_like_sha256(token_str.lower())
                    else stable_sha256(token_str)
                )

            binding_snapshot = _canonical_binding_snapshot(
                action.get("binding_snapshot")
                if "binding_snapshot" in action
                else action.get("binding")
            )

            line_ref = f"{path.name}:L{int(line_no)}"
            refs.add(line_ref)
            events.append(
                {
                    "step_idx": int(step_idx) if step_idx is not None else None,
                    "sink_type": sink_type,
                    "decision": decision,
                    "consent_token_hash": token_hash,
                    "binding_snapshot": binding_snapshot,
                    "evidence_refs": [line_ref],
                }
            )

    events.sort(
        key=lambda e: (
            int(e.get("step_idx")) if isinstance(e.get("step_idx"), int) else -1,
            str(e.get("sink_type") or ""),
        )
    )
    return events, refs


class ConsentTraceDetector(Detector):
    detector_id = "consent_trace"
    produces_fact_ids = ("fact.consent_trace",)

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        _ = case_ctx
        evidence_dir: Path = Path(getattr(pack, "evidence_dir", pack))

        # 1) Preferred: consent_trace.jsonl (future harness/agent writer)
        path = evidence_dir / "consent_trace.jsonl"
        if path.exists():
            events, refs = _try_parse_consent_events_from_jsonl(path)
            if events:
                return [
                    Fact(
                        fact_id="fact.consent_trace",
                        oracle_source="trajectory_declared",
                        evidence_refs=sorted(refs),
                        payload={
                            "consent_events": events,
                            "has_structured_consent": True,
                            "source": path.name,
                        },
                    )
                ]

        # 2) Fallback: best-effort extraction from agent_action_trace.jsonl
        agent_action = evidence_dir / "agent_action_trace.jsonl"
        if agent_action.exists():
            events, refs = _try_parse_consent_events_from_agent_action_trace(agent_action)
            if events:
                return [
                    Fact(
                        fact_id="fact.consent_trace",
                        oracle_source="trajectory_declared",
                        evidence_refs=sorted(refs),
                        payload={
                            "consent_events": events,
                            "has_structured_consent": True,
                            "source": agent_action.name,
                        },
                    )
                ]

        # 3) No structured consent evidence available.
        return []
