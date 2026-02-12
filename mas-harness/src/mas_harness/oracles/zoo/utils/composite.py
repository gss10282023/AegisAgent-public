"""Composite oracle composition helpers.

Step 5.1: CompositeOracle (multi-factor hard verdict).

Motivation: single-source oracles can be spoofed or polluted by stale state.
CompositeOracle upgrades success definition to require multiple independent
signals (e.g., Provider + FileHash).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence

from mas_harness.oracles.zoo.base import (
    Oracle,
    OracleContext,
    OracleEvidence,
    decision_from_evidence,
    make_decision,
    make_oracle_event,
    make_query,
    normalize_capabilities_required,
    now_ms,
)
from mas_harness.oracles.zoo.registry import make_oracle as _make_oracle
from mas_harness.oracles.zoo.registry import register_oracle


@dataclass(frozen=True)
class _ChildRef:
    label: str
    oracle: Oracle


def _normalize_children(children: Sequence[_ChildRef]) -> List[_ChildRef]:
    out: List[_ChildRef] = []
    for idx, child in enumerate(children):
        label = str(child.label).strip() or f"child_{idx}"
        out.append(_ChildRef(label=label, oracle=child.oracle))
    return out


def _summarize_child_decisions(
    children: Sequence[Dict[str, Any]], *, kind: str, limit: int = 3
) -> str:
    parts: List[str] = []
    for child in children[: max(0, int(limit))]:
        label = str(child.get("label") or child.get("oracle_id") or "?")
        reason = str(child.get("decision", {}).get("reason", "") or "")
        parts.append(f"{label}: {reason}".strip())
    suffix = ""
    if len(children) > limit:
        suffix = f" (+{len(children) - limit} more)"
    joined = "; ".join(parts).strip()
    if not joined:
        joined = f"{len(children)} child oracle(s) {kind}"
    return joined + suffix


class CompositeOracle(Oracle):
    """Compose multiple child oracles into a single hard verdict.

    Currently supports `all_of`:
      - Any conclusive child failure => composite fail (conclusive).
      - Otherwise, if any child is inconclusive => composite inconclusive.
      - Otherwise (all conclusive successes) => composite success.
    """

    oracle_id = "composite_oracle"
    oracle_name = "composite_oracle"
    oracle_type = "hard"

    def __init__(
        self,
        *,
        all_of: Sequence[_ChildRef],
        short_circuit_on_fail: bool = False,
    ) -> None:
        children = _normalize_children(all_of)
        if not children:
            raise ValueError("CompositeOracle requires at least 1 child oracle")
        self._children = children
        self._short_circuit_on_fail = bool(short_circuit_on_fail)

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        evidence: OracleEvidence = []
        for child in self._children:
            evidence.extend(child.oracle.pre_check(ctx))
        return evidence

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        evidence: OracleEvidence = []
        child_summaries: List[Dict[str, Any]] = []

        failed_children: List[Dict[str, Any]] = []
        inconclusive_children: List[Dict[str, Any]] = []

        for child in self._children:
            child_ev = child.oracle.post_check(ctx)
            child_decision = decision_from_evidence(child_ev, oracle_id=child.oracle.oracle_id)
            evidence.extend(child_ev)

            summary = {
                "label": child.label,
                "oracle_id": getattr(child.oracle, "oracle_id", "unknown"),
                "oracle_type": getattr(child.oracle, "oracle_type", "unknown"),
                "decision": child_decision,
            }
            child_summaries.append(summary)

            conclusive = bool(child_decision.get("conclusive", False))
            success = bool(child_decision.get("success", False))
            if conclusive and not success:
                failed_children.append(summary)
                if self._short_circuit_on_fail:
                    break
            elif not conclusive:
                inconclusive_children.append(summary)

        if failed_children:
            final = make_decision(
                success=False,
                score=0.0,
                reason=(
                    "composite all_of failed: "
                    f"{_summarize_child_decisions(failed_children, kind='failed')}"
                ),
                conclusive=True,
            )
        elif inconclusive_children:
            final = make_decision(
                success=False,
                score=0.0,
                reason=(
                    "composite all_of inconclusive: "
                    f"{_summarize_child_decisions(inconclusive_children, kind='inconclusive')}"
                ),
                conclusive=False,
            )
        else:
            final = make_decision(
                success=True,
                score=1.0,
                reason="composite all_of success (all child oracles succeeded)",
                conclusive=True,
            )

        caps = sorted(
            set(
                normalize_capabilities_required(getattr(self, "capabilities_required", ()))
                + [
                    cap
                    for child in self._children
                    for cap in normalize_capabilities_required(
                        getattr(child.oracle, "capabilities_required", ())
                    )
                ]
            )
        )

        child_ids = ",".join(
            [f"{c.label}={getattr(c.oracle, 'oracle_id', 'unknown')}" for c in self._children]
        )
        evidence.append(
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[
                    make_query(
                        query_type="custom",
                        cmd=f"composite.all_of({child_ids})",
                        timeout_ms=0,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={
                    "mode": "all_of",
                    "short_circuit_on_fail": self._short_circuit_on_fail,
                    "children": child_summaries,
                },
                result_preview={
                    "mode": "all_of",
                    "children": [
                        {
                            "label": c.get("label"),
                            "oracle_id": c.get("oracle_id"),
                            "conclusive": c.get("decision", {}).get("conclusive"),
                            "success": c.get("decision", {}).get("success"),
                            "reason": c.get("decision", {}).get("reason"),
                        }
                        for c in child_summaries
                    ],
                    "final": final,
                },
                anti_gaming_notes=[
                    (
                        "Composite oracle: requires multiple independent signals (all_of) "
                        "to reduce single-source spoofing and stale-state false positives."
                    ),
                ],
                decision=final,
                capabilities_required=caps,
            )
        )
        return evidence


def _parse_child_list(cfg: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    all_of = cfg.get("all_of")
    if all_of is None:
        all_of = cfg.get("children")
    if not isinstance(all_of, (list, tuple)) or not all_of:
        raise ValueError("CompositeOracle requires non-empty 'all_of' (or 'children') list")
    children: List[Mapping[str, Any]] = []
    for i, item in enumerate(all_of):
        if not isinstance(item, Mapping):
            raise ValueError(f"CompositeOracle child[{i}] must be an object")
        children.append(item)
    return children


@register_oracle(CompositeOracle.oracle_id)
def _make_composite_oracle(cfg: Mapping[str, Any]) -> Oracle:
    children_cfg = _parse_child_list(cfg)
    children: List[_ChildRef] = []
    for idx, child_cfg in enumerate(children_cfg):
        label = (
            child_cfg.get("label") or child_cfg.get("name") or child_cfg.get("id") or f"child_{idx}"
        )
        children.append(_ChildRef(label=str(label), oracle=_make_oracle(dict(child_cfg))))
    return CompositeOracle(
        all_of=children,
        short_circuit_on_fail=bool(cfg.get("short_circuit_on_fail", False)),
    )


@register_oracle("CompositeOracle")
def _make_composite_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_composite_oracle(cfg)


@register_oracle("composite")
def _make_composite_oracle_plugin(cfg: Mapping[str, Any]) -> Oracle:
    return _make_composite_oracle(cfg)
