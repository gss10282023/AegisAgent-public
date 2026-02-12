"""Oracle Zoo base interfaces.

Phase 2 Step 0 goal: keep all oracle implementations under `mas_harness.oracles.zoo`.

Phase 2 Step 1: freeze the minimal Oracle interface + OracleEvidence v0 contract
so evidence is auditable and reproducible (stable digests, explicit queries).
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from mas_harness.oracles.zoo.utils.hashing import stable_sha256
from mas_harness.oracles.zoo.utils.time_window import EpisodeTime

OracleEvent = Dict[str, Any]
OracleEvidence = List[OracleEvent]


ORACLE_EVIDENCE_SCHEMA_VERSION = "0"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class OracleContext:
    """Inputs passed to an Oracle.

    This is intentionally minimal for Phase 2 Step 1. Future steps may add
    optional fields (e.g., time window helpers, bundle paths, capabilities probe).
    """

    task_spec: Mapping[str, Any]
    controller: Any
    serial: Optional[str] = None
    episode_time: EpisodeTime | None = None
    episode_dir: Path | None = None

    @classmethod
    def from_task_and_controller(
        cls,
        *,
        task_spec: Mapping[str, Any],
        controller: Any,
        episode_time: EpisodeTime | None = None,
        episode_dir: Path | str | None = None,
    ) -> "OracleContext":
        serial = getattr(controller, "serial", None)
        if not isinstance(serial, str) or not serial:
            serial = None
        resolved_episode_dir = Path(episode_dir) if episode_dir is not None else None
        return cls(
            task_spec=task_spec,
            controller=controller,
            serial=serial,
            episode_time=episode_time,
            episode_dir=resolved_episode_dir,
        )


def now_ms() -> int:
    return int(time.time() * 1000)


def digest(obj: Any) -> str:
    return stable_sha256(obj)


def make_query(
    *,
    query_type: str,
    timeout_ms: int,
    serial: Optional[str] = None,
    cmd: Optional[str] = None,
    sql: Optional[str] = None,
    path: Optional[str] = None,
    uri: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {
        "type": str(query_type),
        "timeout_ms": int(timeout_ms),
    }
    if serial is not None:
        query["serial"] = serial
    if cmd is not None:
        query["cmd"] = cmd
    if sql is not None:
        query["sql"] = sql
    if path is not None:
        query["path"] = path
    if uri is not None:
        query["uri"] = uri
    if extra:
        query.update(extra)
    return query


def make_decision(*, success: bool, score: float, reason: str, conclusive: bool) -> Dict[str, Any]:
    return {
        "success": bool(success),
        "score": float(score),
        "reason": str(reason),
        "conclusive": bool(conclusive),
    }


def normalize_capabilities_required(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(v) for v in value]
    except TypeError:
        return [str(value)]


def make_oracle_event(
    *,
    oracle_name: str,
    oracle_type: str,
    phase: str,
    queries: Sequence[Mapping[str, Any]],
    result_for_digest: Any,
    anti_gaming_notes: Sequence[str],
    decision: Mapping[str, Any],
    capabilities_required: Sequence[str],
    oracle_id: Optional[str] = None,
    evidence_schema_version: str = ORACLE_EVIDENCE_SCHEMA_VERSION,
    artifacts: Optional[Sequence[Mapping[str, Any]]] = None,
    result_preview: Any | None = None,
    ts_ms: Optional[int] = None,
    **extra: Any,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "oracle_name": oracle_name,
        "oracle_type": oracle_type,
        "phase": phase,
        "queries": list(queries),
        "result_digest": digest(result_for_digest),
        "anti_gaming_notes": list(anti_gaming_notes),
        "decision": dict(decision),
        "capabilities_required": list(capabilities_required),
        "evidence_schema_version": evidence_schema_version,
    }
    if oracle_id is not None:
        event["oracle_id"] = oracle_id
    if artifacts is not None:
        event["artifacts"] = list(artifacts)
    if result_preview is not None:
        event["result_preview"] = result_preview
    if ts_ms is not None:
        event["ts_ms"] = int(ts_ms)
    if extra:
        event.update(extra)
    return event


def oracle_event_v0_errors(event: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []

    required = [
        "oracle_name",
        "oracle_type",
        "phase",
        "queries",
        "result_digest",
        "anti_gaming_notes",
        "decision",
        "capabilities_required",
        "evidence_schema_version",
    ]
    for key in required:
        if key not in event:
            errors.append(f"missing field: {key}")

    oracle_name = event.get("oracle_name")
    if not isinstance(oracle_name, str) or not oracle_name:
        errors.append("oracle_name must be a non-empty string")

    oracle_type = event.get("oracle_type")
    if not isinstance(oracle_type, str) or not oracle_type:
        errors.append("oracle_type must be a non-empty string")

    phase = event.get("phase")
    if phase not in {"pre", "post"}:
        errors.append("phase must be 'pre' or 'post'")

    queries = event.get("queries")
    if not isinstance(queries, list) or not queries:
        errors.append("queries must be a non-empty list")
    else:
        for i, q in enumerate(queries):
            if not isinstance(q, MappingABC):
                errors.append(f"queries[{i}] must be an object")
                continue
            q_type = q.get("type")
            if not isinstance(q_type, str) or not q_type:
                errors.append(f"queries[{i}].type must be a non-empty string")
            timeout_ms = q.get("timeout_ms")
            if not isinstance(timeout_ms, int) or timeout_ms < 0:
                errors.append(f"queries[{i}].timeout_ms must be an int >= 0")
            has_locator = any(
                isinstance(q.get(k), str) and q.get(k) for k in ("cmd", "sql", "path", "uri")
            )
            if not has_locator:
                errors.append(f"queries[{i}] must include one of cmd/sql/path/uri")

    result_digest = event.get("result_digest")
    if not isinstance(result_digest, str) or not _SHA256_HEX_RE.match(result_digest):
        errors.append("result_digest must be a sha256 hex string")

    anti = event.get("anti_gaming_notes")
    if not isinstance(anti, list) or not anti or not all(isinstance(x, str) and x for x in anti):
        errors.append("anti_gaming_notes must be a non-empty list[str]")

    decision = event.get("decision")
    if not isinstance(decision, MappingABC):
        errors.append("decision must be an object")
    else:
        for k, t in (("success", bool), ("conclusive", bool), ("reason", str)):
            v = decision.get(k)
            if not isinstance(v, t) or (t is str and not v):
                errors.append(f"decision.{k} must be {t.__name__}")
        score = decision.get("score")
        if not isinstance(score, (int, float)) or not (0.0 <= float(score) <= 1.0):
            errors.append("decision.score must be a number in [0, 1]")

    caps = event.get("capabilities_required")
    if not isinstance(caps, list) or not all(isinstance(x, str) and x for x in caps):
        errors.append("capabilities_required must be list[str] (may be empty)")

    ver = event.get("evidence_schema_version")
    if ver != ORACLE_EVIDENCE_SCHEMA_VERSION:
        errors.append(f"evidence_schema_version must be '{ORACLE_EVIDENCE_SCHEMA_VERSION}'")

    return errors


def assert_oracle_event_v0(event: Mapping[str, Any]) -> None:
    errors = oracle_event_v0_errors(event)
    if errors:
        raise ValueError("OracleEvidence v0 contract violation: " + "; ".join(errors))


def find_decision_event(
    evidence: Sequence[Mapping[str, Any]],
    *,
    oracle_id: Optional[str] = None,
    phase: str = "post",
) -> Optional[Mapping[str, Any]]:
    for ev in reversed(evidence):
        if ev.get("phase") != phase:
            continue
        if oracle_id is not None:
            ev_id = ev.get("oracle_id")
            ev_name = ev.get("oracle_name")
            if isinstance(ev_id, str):
                if ev_id != oracle_id:
                    continue
            elif ev_name != oracle_id:
                continue
        decision = ev.get("decision")
        if isinstance(decision, MappingABC):
            return ev
    return None


def decision_from_evidence(
    evidence: Sequence[Mapping[str, Any]],
    *,
    oracle_id: Optional[str] = None,
    phase: str = "post",
) -> Dict[str, Any]:
    ev = find_decision_event(evidence, oracle_id=oracle_id, phase=phase)
    if ev is None:
        return make_decision(
            success=False,
            score=0.0,
            reason="missing oracle decision event",
            conclusive=False,
        )
    decision = ev.get("decision")
    if not isinstance(decision, MappingABC):
        return make_decision(
            success=False,
            score=0.0,
            reason="missing decision payload",
            conclusive=False,
        )
    return dict(decision)


class Oracle:
    """Oracle base class (Phase 2 Step 1 contract)."""

    oracle_id: str = "oracle"
    oracle_name: str = "oracle"
    oracle_type: str = "hard"  # hard|soft|hybrid
    capabilities_required: Sequence[str] = ()
    evidence_schema_version: str = ORACLE_EVIDENCE_SCHEMA_VERSION

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        """Collect pre-run evidence (optional)."""

        _ = ctx
        return []

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        """Return post-run OracleEvidence (must include a decision event)."""

        raise NotImplementedError
