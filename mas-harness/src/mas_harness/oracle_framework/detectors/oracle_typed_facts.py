from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from mas_harness.evidence import stable_sha256
from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.detectors._jsonl import iter_jsonl_objects
from mas_harness.oracle_framework.detectors.oracle_adapters.registry import select_adapter
from mas_harness.oracle_framework.types import Fact

_FACT_SEGMENT_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _safe_fact_segment(value: Any, *, default: str = "unknown") -> str:
    raw = _nonempty_str(value) or ""
    out = "".join(ch if ch in _FACT_SEGMENT_SAFE_CHARS else "_" for ch in raw)
    out = out.strip("_")
    return out or default


def _extract_success_oracle_name(case_ctx: Any) -> Optional[str]:
    if not isinstance(case_ctx, Mapping):
        return None
    raw = case_ctx.get("success_oracle_name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    task = case_ctx.get("task")
    if isinstance(task, Mapping):
        cfg = task.get("success_oracle") or task.get("oracle") or {}
        if isinstance(cfg, Mapping):
            plugin = cfg.get("plugin") or cfg.get("type")
            if isinstance(plugin, str) and plugin.strip():
                return plugin.strip()
    return None


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


def _event_evidence_refs(
    path_name: str, line_no: int, *, artifacts: list[dict[str, Any]]
) -> list[str]:
    refs: set[str] = {f"{path_name}:L{int(line_no)}"}
    for a in artifacts:
        p = _nonempty_str(a.get("path"))
        if p is None:
            continue
        refs.add(f"artifact:{p}")
    return sorted(refs)


def _decision_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    if isinstance(value.get("success"), bool):
        out["success"] = bool(value.get("success"))
    if isinstance(value.get("conclusive"), bool):
        out["conclusive"] = bool(value.get("conclusive"))
    score = value.get("score")
    if isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0:
        out["score"] = float(score)
    reason = _nonempty_str(value.get("reason"))
    if reason is not None:
        out["reason"] = reason[:200]
    return out or None


def _preview_meta(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    meta: dict[str, Any] = {"type": type(value).__name__}
    if isinstance(value, str):
        meta["len"] = len(value)
    elif isinstance(value, list):
        meta["len"] = len(value)
    elif isinstance(value, dict):
        keys = [str(k) for k in value.keys()]
        meta["keys"] = sorted(keys)[:50]
        meta["len"] = len(keys)
    return meta


class OracleTypedFactsDetector(Detector):
    detector_id = "oracle_typed_facts"
    evidence_required = ("oracle_trace.jsonl",)
    produces_fact_ids = ()

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        evidence_dir: Path = Path(getattr(pack, "evidence_dir", pack))
        path = evidence_dir / "oracle_trace.jsonl"
        if not path.exists():
            return []

        # Ensure adapter modules are imported (side-effect registration).
        from mas_harness.oracle_framework.detectors import (
            oracle_adapters as _oracle_adapters,  # noqa: F401
        )

        facts: list[Fact] = []

        success_oracle_name = _extract_success_oracle_name(case_ctx)
        last_success_event: tuple[int, dict[str, Any]] | None = None

        for line_no, obj in iter_jsonl_objects(path):
            oracle_name = _nonempty_str(obj.get("oracle_name")) or "unknown"
            phase = _nonempty_str(obj.get("phase")) or "unknown"

            artifacts = _canonical_artifacts(obj)
            refs = _event_evidence_refs(path.name, int(line_no), artifacts=artifacts)

            # Generic replay typed fact for every oracle event (PII-safe: no raw preview).
            queries = obj.get("queries")
            query_types: list[str] = []
            if isinstance(queries, list):
                for q in queries:
                    if isinstance(q, dict):
                        t = _nonempty_str(q.get("type"))
                        if t is not None:
                            query_types.append(t)
            query_types = sorted(set(query_types))
            queries_digest = stable_sha256(queries) if isinstance(queries, list) else None

            preview = obj.get("result_preview")
            generic_payload = {
                "oracle_name": oracle_name,
                "phase": phase,
                "oracle_id": _nonempty_str(obj.get("oracle_id")),
                "oracle_type": _nonempty_str(obj.get("oracle_type")),
                "decision": _decision_summary(obj.get("decision")),
                "result_digest": _nonempty_str(obj.get("result_digest")),
                "result_preview_digest": stable_sha256(preview) if preview is not None else None,
                "result_preview_meta": _preview_meta(preview),
                "queries_count": len(queries) if isinstance(queries, list) else None,
                "query_types": query_types,
                "queries_digest": queries_digest,
                "artifacts": artifacts,
                "capabilities_required": sorted(
                    {str(c) for c in (obj.get("capabilities_required") or []) if str(c).strip()}
                )
                if isinstance(obj.get("capabilities_required"), list)
                else [],
                "missing_capabilities": sorted(
                    {str(c) for c in (obj.get("missing_capabilities") or []) if str(c).strip()}
                )
                if isinstance(obj.get("missing_capabilities"), list)
                else [],
                "anti_gaming_notes_digest": stable_sha256(obj.get("anti_gaming_notes"))
                if isinstance(obj.get("anti_gaming_notes"), list)
                else None,
            }

            facts.append(
                Fact(
                    fact_id=(
                        "fact.oracle.typed/"
                        f"{_safe_fact_segment(oracle_name)}/{_safe_fact_segment(phase)}/L{int(line_no)}"
                    ),
                    oracle_source="device_query",
                    evidence_refs=refs,
                    payload=generic_payload,
                )
            )

            # Semantic typed facts via adapter matching (PII redaction happens inside adapters).
            adapter = select_adapter(obj)
            if adapter is not None:
                try:
                    adapted = adapter.adapt(obj, line_no=int(line_no), evidence_dir=evidence_dir)
                except Exception:
                    adapted = []
                for fact in adapted:
                    # Enforce traceability: always include the line ref (+ artifact refs).
                    merged_refs = sorted(set(refs) | set(getattr(fact, "evidence_refs", []) or []))
                    payload = dict(getattr(fact, "payload", {}) or {})
                    facts.append(
                        Fact(
                            fact_id=str(fact.fact_id),
                            schema_version=fact.schema_version,
                            oracle_source=str(
                                getattr(fact, "oracle_source", "device_query") or "device_query"
                            ),
                            evidence_refs=merged_refs,
                            payload=payload,
                        )
                    )

            # Track the latest success-oracle decision event for a stable episode-level fact.
            if (
                success_oracle_name is not None
                and oracle_name == success_oracle_name
                and phase == "post"
            ):
                last_success_event = (int(line_no), obj)

        # Coverage hard threshold: a stable semantic fact for the task success oracle decision.
        if last_success_event is not None:
            line_no, ev = last_success_event
            artifacts = _canonical_artifacts(ev)
            refs = _event_evidence_refs(path.name, int(line_no), artifacts=artifacts)
            preview = ev.get("result_preview")
            facts.append(
                Fact(
                    fact_id="fact.task.success_oracle_decision",
                    oracle_source="device_query",
                    evidence_refs=refs,
                    payload={
                        "success_oracle_name": success_oracle_name,
                        "oracle_name": _nonempty_str(ev.get("oracle_name")),
                        "phase": _nonempty_str(ev.get("phase")),
                        "line": int(line_no),
                        "oracle_id": _nonempty_str(ev.get("oracle_id")),
                        "oracle_type": _nonempty_str(ev.get("oracle_type")),
                        "decision": _decision_summary(ev.get("decision")),
                        "result_digest": _nonempty_str(ev.get("result_digest")),
                        "result_preview_digest": stable_sha256(preview)
                        if preview is not None
                        else None,
                        "artifacts": artifacts,
                    },
                )
            )

        return facts
