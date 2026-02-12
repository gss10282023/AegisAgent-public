"""No-op oracle plugin for agentctl NL runs.

This oracle intentionally does not assert task success. It exists so nl-mode
cases can remain schema-valid (task requires a success_oracle) while keeping
`oracle_source=none` semantics.
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


class NoOracle(Oracle):
    oracle_id = "none"
    oracle_name = "none"
    oracle_type = "soft"
    capabilities_required = ()

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        del ctx
        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[make_query(query_type="none", cmd="noop", timeout_ms=0)],
                result_for_digest={"oracle": "none"},
                result_preview={"oracle": "none"},
                anti_gaming_notes=[
                    "No-op oracle: does not assert task success; intended for debugging runs."
                ],
                decision=make_decision(
                    success=False,
                    score=0.0,
                    reason="no hard oracle (agentctl nl)",
                    conclusive=False,
                ),
                capabilities_required=[],
            )
        ]


@register_oracle(NoOracle.oracle_id)
def _make_no_oracle(cfg: Mapping[str, Any]) -> Oracle:
    del cfg
    return NoOracle()
