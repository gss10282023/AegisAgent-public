from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from mas_harness.evidence import stable_sha256
from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _extract_tokens_from_eval(ev: Mapping[str, Any]) -> list[str]:
    tokens: list[str] = []

    raw_list = ev.get("canary_tokens")
    if isinstance(raw_list, list):
        tokens.extend([s for s in (_nonempty_str(x) for x in raw_list) if s is not None])

    raw_one = _nonempty_str(ev.get("canary_token"))
    if raw_one is not None:
        tokens.append(raw_one)

    raw_canary = ev.get("canary")
    if isinstance(raw_canary, Mapping):
        raw_tokens = raw_canary.get("tokens")
        if isinstance(raw_tokens, list):
            tokens.extend([s for s in (_nonempty_str(x) for x in raw_tokens) if s is not None])

    return sorted(set(tokens))


def _extract_declared_sinks(ev: Mapping[str, Any], policy: Mapping[str, Any]) -> list[str]:
    sinks: set[str] = set()
    raw_canary = ev.get("canary")
    if isinstance(raw_canary, Mapping):
        for key in ("declared_sinks", "sinks", "sink_types"):
            raw = raw_canary.get(key)
            if isinstance(raw, list):
                for item in raw:
                    s = _nonempty_str(item)
                    if s is not None:
                        sinks.add(s)
    raw = ev.get("declared_sinks")
    if isinstance(raw, list):
        for item in raw:
            s = _nonempty_str(item)
            if s is not None:
                sinks.add(s)

    writable_set = policy.get("writable_set")
    if isinstance(writable_set, Mapping):
        raw_ws = (
            writable_set.get("WritableSinks")
            if "WritableSinks" in writable_set
            else writable_set.get("writable_sinks")
        )
        if isinstance(raw_ws, list):
            for item in raw_ws:
                s = _nonempty_str(item)
                if s is not None:
                    sinks.add(s)
    return sorted(sinks)


def _config_evidence_refs(case_ctx: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(case_ctx, Mapping):
        for k in ("policy_path", "eval_path"):
            v = _nonempty_str(case_ctx.get(k))
            if v is not None:
                refs.append(v)
    refs.extend(["policy.yaml", "eval.yaml"])
    return sorted({r for r in refs if r})


class CanaryConfigDetector(Detector):
    detector_id = "canary_config"
    produces_fact_ids = ("fact.canary_tokens",)

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        _ = pack
        if not isinstance(case_ctx, Mapping):
            return []

        ev = case_ctx.get("eval")
        policy = case_ctx.get("policy")
        ev_map: Mapping[str, Any] = ev if isinstance(ev, Mapping) else {}
        policy_map: Mapping[str, Any] = policy if isinstance(policy, Mapping) else {}

        tokens = _extract_tokens_from_eval(ev_map)
        if not tokens:
            return []

        tokens_hashes = sorted({stable_sha256(t) for t in tokens})
        sinks = _extract_declared_sinks(ev_map, policy_map)

        return [
            Fact(
                fact_id="fact.canary_tokens",
                oracle_source="trajectory_declared",
                evidence_refs=_config_evidence_refs(case_ctx),
                payload={"tokens_hashes": tokens_hashes, "declared_sinks": sinks},
            )
        ]
