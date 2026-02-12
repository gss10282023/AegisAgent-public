from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from mas_harness.evidence import stable_sha256
from mas_harness.evidence.evidence_pack import resolve_episode_evidence_dir
from mas_harness.oracle_framework.engine import compile_enabled_assertions, run_audit_first
from mas_harness.oracle_framework.io import write_json_atomic
from mas_harness.oracle_framework.types import AssertionResult, Fact
from mas_harness.spec import LoadedCaseSpecs


def _try_load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _coerce_float_rate(numer: int, denom: int) -> float:
    return float(numer) / float(denom) if denom else 0.0


def _is_safety_assertion_id(assertion_id: str) -> bool:
    return assertion_id.startswith("SA_")


_ALLOWED_TRUST_LEVELS = {"tcb_captured", "agent_reported", "unknown"}
_ALLOWED_ORACLE_SOURCES = {"device_query", "trajectory_declared", "none"}
_ALLOWED_ACTION_TRACE_LEVELS = {"L0", "L1", "L2", "L3", "none"}


def _normalize_trust_level(value: Any) -> str:
    s = str(value or "").strip().lower()
    if s in _ALLOWED_TRUST_LEVELS:
        return s
    return "unknown"


def _normalize_oracle_source(value: Any) -> str:
    s = str(value or "").strip().lower()
    if s in _ALLOWED_ORACLE_SOURCES:
        return s
    return "none"


def _normalize_action_trace_level(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return "none"
    if s.lower() == "none":
        return "none"
    s = s.upper()
    if s in _ALLOWED_ACTION_TRACE_LEVELS:
        return s
    return "none"


def _extract_fact_payload_str(facts: Sequence[Fact], *, fact_id: str, key: str) -> str | None:
    for fact in facts:
        if fact.fact_id != fact_id:
            continue
        payload = fact.payload if isinstance(fact.payload, Mapping) else {}
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _is_core_trusted(*, trust_level: str, oracle_source: str) -> bool:
    return trust_level == "tcb_captured" and oracle_source == "device_query"


def _count_results(results: Sequence[AssertionResult]) -> dict[str, int]:
    counts = {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0}
    for r in results:
        if r.result in counts:
            counts[r.result] += 1
    return counts


def _violation_from_result(r: AssertionResult) -> dict[str, Any]:
    return {
        "assertion_id": r.assertion_id,
        "mapped_sp": r.mapped_sp,
        "mapped_primitive": r.mapped_primitive,
        "mapped_boundary": r.mapped_boundary,
        "impact_level": r.impact_level,
        "severity": r.severity,
        "risk_weight_bucket": r.risk_weight_bucket,
        "evidence_refs": list(r.evidence_refs),
    }


def _audit_summary_from_results(results: Sequence[AssertionResult]) -> dict[str, Any]:
    total = len(results)
    applicable_true = sum(1 for r in results if r.applicable is True)
    inconclusive = sum(1 for r in results if r.result == "INCONCLUSIVE")

    success_results = [r for r in results if r.assertion_id == "SuccessOracleAssertion"]
    safety_results = [r for r in results if _is_safety_assertion_id(r.assertion_id)]

    violations = [
        _violation_from_result(r)
        for r in safety_results
        if r.result == "FAIL" and r.applicable is True
    ]

    audit: dict[str, Any] = {
        "assertion_applicable_rate": _coerce_float_rate(applicable_true, total),
        "assertion_inconclusive_rate": _coerce_float_rate(inconclusive, total),
        "success_assertions_summary": {
            "total": len(success_results),
            **_count_results(success_results),
        },
        "safety_assertions_summary": {
            "total": len(safety_results),
            **_count_results(safety_results),
        },
        "assertion_applicable_rate_counts": {
            "applicable_true": int(applicable_true),
            "total": int(total),
        },
        "assertion_inconclusive_rate_counts": {
            "inconclusive": int(inconclusive),
            "total": int(total),
        },
        "violations": violations,
    }
    return audit


def _update_summary_audit_fields(
    summary: MutableMapping[str, Any], audit: Mapping[str, Any]
) -> None:
    summary["audit"] = dict(audit)
    if isinstance(summary.get("violations"), list):
        summary["violations"] = list(audit.get("violations") or [])


def _summary_paths_for_episode_dir(episode_dir: Path) -> list[Path]:
    episode_dir = Path(episode_dir)
    evidence_dir = resolve_episode_evidence_dir(episode_dir)

    candidates: list[Path] = []
    candidates.append(evidence_dir / "summary.json")

    if evidence_dir != episode_dir:
        candidates.append(episode_dir / "summary.json")

    if episode_dir.name == "evidence":
        candidates.append(episode_dir.parent / "summary.json")

    out: list[Path] = []
    seen: set[Path] = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        if p.exists():
            out.append(p)
    return out


@dataclass(frozen=True)
class AuditEngineOutput:
    facts: list[Fact]
    assertions: list[AssertionResult]
    audit_summary: dict[str, Any]


class AuditEngine:
    """Phase4 audit-first orchestrator: detectors → facts, assertions → results, summary audit."""

    def run(
        self,
        *,
        episode_dir: Path | str,
        case_specs: LoadedCaseSpecs | None = None,
        case_ctx: Mapping[str, Any] | None = None,
    ) -> AuditEngineOutput:
        episode_dir = Path(episode_dir)

        audit_ctx: dict[str, Any] = dict(case_ctx) if isinstance(case_ctx, Mapping) else {}
        if case_specs is not None:
            audit_ctx.setdefault("task", case_specs.task)
            audit_ctx.setdefault("policy", case_specs.policy)
            audit_ctx.setdefault("eval", case_specs.eval)
            audit_ctx.setdefault("policy_path", str(case_specs.paths.policy))
            audit_ctx.setdefault("eval_path", str(case_specs.paths.eval))
            if case_specs.attack is not None:
                audit_ctx.setdefault("attack", case_specs.attack)
                impact = (
                    case_specs.attack.get("impact_level")
                    if isinstance(case_specs.attack, Mapping)
                    else None
                )
                if isinstance(impact, str) and impact.strip():
                    audit_ctx.setdefault("impact_level", impact.strip())

        policy = audit_ctx.get("policy") if isinstance(audit_ctx.get("policy"), Mapping) else {}
        ev = audit_ctx.get("eval") if isinstance(audit_ctx.get("eval"), Mapping) else {}
        enabled_assertions, enabled_sources = compile_enabled_assertions(policy, ev)

        facts, results = run_audit_first(
            episode_dir, audit_ctx, enabled_assertions=enabled_assertions
        )
        audit_summary = _audit_summary_from_results(results)

        trust_level = _normalize_trust_level(
            _extract_fact_payload_str(
                facts,
                fact_id="fact.evidence_trust_level",
                key="evidence_trust_level",
            )
        )
        oracle_source = _normalize_oracle_source(
            _extract_fact_payload_str(
                facts,
                fact_id="fact.oracle_source_summary",
                key="oracle_source",
            )
        )
        action_trace_level = _normalize_action_trace_level(
            _extract_fact_payload_str(
                facts,
                fact_id="fact.action_trace_level",
                key="action_trace_level",
            )
        )
        audit_summary.update(
            {
                "trust_level": trust_level,
                "oracle_source": oracle_source,
                "action_trace_level": action_trace_level,
                "is_core_trusted": _is_core_trusted(
                    trust_level=trust_level, oracle_source=oracle_source
                ),
            }
        )

        def _params_digest(params: Any) -> str:
            try:
                return stable_sha256(params)
            except Exception:
                return stable_sha256({"params_repr": repr(params)[:500]})

        audit_summary["enabled_assertions"] = [
            {
                "assertion_id": cfg.assertion_id,
                "params_digest": _params_digest(cfg.params),
                "enabled_source": str(enabled_sources.get(cfg.assertion_id) or "baseline"),
            }
            for cfg in enabled_assertions
        ]

        for summary_path in _summary_paths_for_episode_dir(episode_dir):
            summary_obj = _try_load_json_object(summary_path) or {}
            audit_out = dict(audit_summary)

            # Reporting should not need to parse facts.jsonl; fall back to summary metadata when
            # needed.
            if audit_out.get("trust_level") == "unknown":
                audit_out["trust_level"] = _normalize_trust_level(
                    summary_obj.get("evidence_trust_level")
                )
            if audit_out.get("oracle_source") == "none":
                audit_out["oracle_source"] = _normalize_oracle_source(
                    summary_obj.get("oracle_source")
                )
            if audit_out.get("action_trace_level") == "none":
                audit_out["action_trace_level"] = _normalize_action_trace_level(
                    summary_obj.get("action_trace_level")
                )

            audit_out["is_core_trusted"] = _is_core_trusted(
                trust_level=str(audit_out.get("trust_level") or "unknown"),
                oracle_source=str(audit_out.get("oracle_source") or "none"),
            )

            _update_summary_audit_fields(summary_obj, audit_out)
            write_json_atomic(summary_path, summary_obj)

        return AuditEngineOutput(
            facts=list(facts), assertions=list(results), audit_summary=audit_summary
        )
