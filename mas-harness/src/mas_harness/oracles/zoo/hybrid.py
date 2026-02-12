"""Hybrid oracle composition."""

from __future__ import annotations

from typing import Any, Mapping

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


class HybridOracle(Oracle):
    """Hybrid oracle: hard as source of truth; soft used only when hard is inconclusive."""

    oracle_id = "hybrid_oracle"
    oracle_name = "hybrid_oracle"
    oracle_type = "hybrid"

    def __init__(self, *, hard: Oracle, soft: Oracle):
        if hard.oracle_type != "hard":
            raise ValueError("hybrid_oracle.hard must be a hard oracle")
        self._hard = hard
        self._soft = soft

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        evidence: OracleEvidence = []
        evidence.extend(self._hard.pre_check(ctx))
        evidence.extend(self._soft.pre_check(ctx))
        return evidence

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        hard_evidence = self._hard.post_check(ctx)
        hard_decision = decision_from_evidence(hard_evidence, oracle_id=self._hard.oracle_id)
        evidence: OracleEvidence = list(hard_evidence)

        soft_decision = None
        if hard_decision.get("conclusive", False):
            final = make_decision(
                success=bool(hard_decision.get("success", False)),
                score=float(hard_decision.get("score", 0.0)),
                reason=f"hard: {hard_decision.get('reason', '')}",
                conclusive=True,
            )
        else:
            soft_evidence = self._soft.post_check(ctx)
            soft_decision = decision_from_evidence(soft_evidence, oracle_id=self._soft.oracle_id)
            evidence.extend(soft_evidence)
            final = make_decision(
                success=bool(soft_decision.get("success", False)),
                score=float(soft_decision.get("score", 0.0)),
                reason=f"hard inconclusive; soft: {soft_decision.get('reason', '')}",
                conclusive=bool(soft_decision.get("conclusive", False)),
            )

        caps = sorted(
            set(
                normalize_capabilities_required(getattr(self, "capabilities_required", ()))
                + normalize_capabilities_required(getattr(self._hard, "capabilities_required", ()))
                + normalize_capabilities_required(getattr(self._soft, "capabilities_required", ()))
            )
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
                        cmd=f"hybrid(hard={self._hard.oracle_id},soft={self._soft.oracle_id})",
                        timeout_ms=0,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={"hard": hard_decision, "soft": soft_decision},
                anti_gaming_notes=[
                    (
                        "Hybrid oracle: uses hard verdict when conclusive; "
                        "otherwise falls back to soft."
                    ),
                ],
                decision=final,
                capabilities_required=caps,
            )
        )
        return evidence


@register_oracle(HybridOracle.oracle_id)
def _make_hybrid_oracle(cfg: Mapping[str, Any]) -> Oracle:
    hard_cfg = cfg.get("hard")
    soft_cfg = cfg.get("soft")
    if not isinstance(hard_cfg, Mapping) or not isinstance(soft_cfg, Mapping):
        raise ValueError("hybrid_oracle requires 'hard' and 'soft' sub-configs")
    return HybridOracle(hard=_make_oracle(hard_cfg), soft=_make_oracle(soft_cfg))
