"""ToyEnv oracles (Phase 0/1 smoke tests).

These exist to keep the harness testable without Android. They still live under
Oracle Zoo so *all* oracle plugins stay in one discoverable folder.
"""

from __future__ import annotations

from typing import Any, Mapping

from mas_harness.oracles.zoo.base import (
    Oracle,
    OracleContext,
    OracleEvidence,
    make_decision,
    make_oracle_event,
    make_query,
    now_ms,
)
from mas_harness.oracles.zoo.registry import register_oracle


class ToySuccessAfterStepsOracle(Oracle):
    oracle_id = "toy_success_after_steps"
    oracle_name = "toy_success_after_steps"
    oracle_type = "hard"

    def __init__(self, *, steps: int):
        if steps < 0:
            raise ValueError("steps must be >= 0")
        self._steps = steps

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        # In the toy env, controller is the env itself.
        step = getattr(ctx.controller, "step", None)
        result = {"env_step": step}
        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="pre",
                queries=[
                    make_query(
                        query_type="custom",
                        cmd="env.step",
                        timeout_ms=0,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest=result,
                result_preview=result,
                anti_gaming_notes=["Toy oracle: checks env.step; cannot be spoofed via UI."],
                decision=make_decision(
                    success=True,
                    score=1.0,
                    reason="collected baseline env.step",
                    conclusive=True,
                ),
                capabilities_required=[],
            )
        ]

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        step = getattr(ctx.controller, "step", None)
        ok = isinstance(step, int) and step >= self._steps
        result = {"env_step": step, "required_steps": self._steps, "ok": ok}
        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[
                    make_query(
                        query_type="custom",
                        cmd=f"env.step >= {self._steps}",
                        timeout_ms=0,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest=result,
                result_preview={"env_step": step, "required_steps": self._steps, "ok": ok},
                anti_gaming_notes=["Toy oracle: checks env.step; cannot be spoofed via UI."],
                decision=make_decision(
                    success=ok,
                    score=1.0 if ok else 0.0,
                    reason=f"env.step={step} (required >= {self._steps})",
                    conclusive=True,
                ),
                capabilities_required=[],
            )
        ]


@register_oracle(ToySuccessAfterStepsOracle.oracle_id)
def _make_toy_success_after_steps(cfg: Mapping[str, Any]) -> Oracle:
    steps = int(cfg.get("steps", 0))
    return ToySuccessAfterStepsOracle(steps=steps)
