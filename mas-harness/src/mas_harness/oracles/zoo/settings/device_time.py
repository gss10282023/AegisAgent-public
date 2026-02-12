"""Device time oracle (Phase 2 Step 5).

This oracle is primarily an infrastructure probe: it captures the device epoch
time via adb so other oracles can enforce a shared time window and avoid stale/
historical false positives.
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
from mas_harness.oracles.zoo.utils.time_window import probe_device_epoch_time_ms


class DeviceTimeOracle(Oracle):
    oracle_id = "device_time"
    oracle_name = "device_time"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(self, *, timeout_ms: int = 1500) -> None:
        self._timeout_ms = int(timeout_ms)

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        epoch_ms, meta = probe_device_epoch_time_ms(ctx.controller, timeout_ms=self._timeout_ms)

        if epoch_ms is None:
            conclusive = False
            success = False
            score = 0.0
            reason = "device epoch time probe failed"
        else:
            conclusive = True
            success = True
            score = 1.0
            reason = "device epoch time probed"

        query_cmd = None
        if isinstance(meta, dict):
            attempt1 = meta.get("attempt1") if "attempt1" in meta else meta
            if isinstance(attempt1, dict):
                query_cmd = attempt1.get("cmd")

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[
                    make_query(
                        query_type="adb_cmd",
                        cmd=f"shell {query_cmd or 'date +%s%3N'}",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={
                    "epoch_ms": epoch_ms,
                    "meta": meta,
                    "episode_start_host_utc_ms": getattr(ctx, "episode_time", None).t0_host_utc_ms
                    if getattr(ctx, "episode_time", None) is not None
                    else None,
                },
                result_preview={
                    "epoch_ms": epoch_ms,
                    "source": (meta.get("source") if isinstance(meta, dict) else None),
                },
                anti_gaming_notes=[
                    (
                        "Infrastructure probe: captures device epoch time so other oracles can "
                        "apply a strict time window and avoid stale/historical false positives."
                    ),
                ],
                decision=make_decision(
                    success=success,
                    score=score,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]


@register_oracle(DeviceTimeOracle.oracle_id)
def _make_device_time(cfg: Mapping[str, Any]) -> Oracle:
    timeout_ms = int(cfg.get("timeout_ms", 1500))
    return DeviceTimeOracle(timeout_ms=timeout_ms)


@register_oracle("DeviceTimeOracle")
def _make_device_time_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_device_time(cfg)
