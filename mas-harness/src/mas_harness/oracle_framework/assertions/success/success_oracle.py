from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from mas_harness.oracle_framework.assertions.base import Assertion
from mas_harness.oracle_framework.store import FactStore
from mas_harness.oracle_framework.types import AssertionResult


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


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


class SuccessOracleAssertion(Assertion):
    assertion_id = "SuccessOracleAssertion"
    required_fact_ids = ()

    def evaluate(self, facts: FactStore, case_ctx: Any) -> AssertionResult:
        oracle_name = _extract_success_oracle_name(case_ctx)
        if oracle_name is None:
            return AssertionResult(
                assertion_id=self.assertion_id,
                result="INCONCLUSIVE",
                severity="low",
                mapped_sp="TASK_SUCCESS",
                mapped_primitive=None,
                mapped_boundary=None,
                impact_level=_nonempty_str(case_ctx.get("impact_level"))
                if isinstance(case_ctx, Mapping)
                else None,
                evidence_refs=[],
                inconclusive_reason="missing_success_oracle_name",
                applicable=False,
            )

        fact_id = f"fact.oracle_event_index/{oracle_name}/post"
        ev_fact = facts.get(fact_id)
        if ev_fact is None:
            return AssertionResult(
                assertion_id=self.assertion_id,
                result="INCONCLUSIVE",
                severity="low",
                mapped_sp="TASK_SUCCESS",
                mapped_primitive=None,
                mapped_boundary=None,
                impact_level=_nonempty_str(case_ctx.get("impact_level"))
                if isinstance(case_ctx, Mapping)
                else None,
                evidence_refs=[],
                inconclusive_reason="missing_fact",
                applicable=True,
            )

        payload = dict(getattr(ev_fact, "payload", {}) or {})
        events = payload.get("events")
        if not isinstance(events, list) or not events:
            return AssertionResult(
                assertion_id=self.assertion_id,
                result="INCONCLUSIVE",
                severity="low",
                mapped_sp="TASK_SUCCESS",
                mapped_primitive=None,
                mapped_boundary=None,
                impact_level=_nonempty_str(case_ctx.get("impact_level"))
                if isinstance(case_ctx, Mapping)
                else None,
                evidence_refs=list(getattr(ev_fact, "evidence_refs", [])),
                inconclusive_reason="missing_oracle_decision",
                applicable=True,
            )

        last = events[-1]
        decision = last.get("decision") if isinstance(last, Mapping) else None
        if not isinstance(decision, Mapping):
            return AssertionResult(
                assertion_id=self.assertion_id,
                result="INCONCLUSIVE",
                severity="low",
                mapped_sp="TASK_SUCCESS",
                mapped_primitive=None,
                mapped_boundary=None,
                impact_level=_nonempty_str(case_ctx.get("impact_level"))
                if isinstance(case_ctx, Mapping)
                else None,
                evidence_refs=list(getattr(ev_fact, "evidence_refs", [])),
                inconclusive_reason="missing_oracle_decision",
                applicable=True,
            )

        conclusive = decision.get("conclusive") is True
        success = decision.get("success") is True
        refs = list(getattr(ev_fact, "evidence_refs", []))

        if not conclusive:
            return AssertionResult(
                assertion_id=self.assertion_id,
                result="INCONCLUSIVE",
                severity="low",
                mapped_sp="TASK_SUCCESS",
                mapped_primitive=None,
                mapped_boundary=None,
                impact_level=_nonempty_str(case_ctx.get("impact_level"))
                if isinstance(case_ctx, Mapping)
                else None,
                evidence_refs=refs,
                inconclusive_reason="oracle_inconclusive",
                applicable=True,
            )

        return AssertionResult(
            assertion_id=self.assertion_id,
            result="PASS" if success else "FAIL",
            severity="low",
            mapped_sp="TASK_SUCCESS",
            mapped_primitive=None,
            mapped_boundary=None,
            impact_level=_nonempty_str(case_ctx.get("impact_level"))
            if isinstance(case_ctx, Mapping)
            else None,
            evidence_refs=refs,
            applicable=True,
        )
