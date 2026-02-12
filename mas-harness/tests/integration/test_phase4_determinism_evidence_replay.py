from __future__ import annotations

import json
from pathlib import Path

from mas_harness.evidence.evidence_pack import ensure_evidence_pack_v0_episode_dir
from mas_harness.oracle_framework.audit_engine import AuditEngine


def _policy_allowed_apps(*, apps: list[str]) -> dict:
    return {
        "readable_set": {
            "ReadableApps": list(apps),
            "ReadableDataTypes": [],
            "ReadableWebOrigins": [],
        },
        "writable_set": {"WritableApps": list(apps), "WritableSinks": [], "WritableWebOrigins": []},
        "flow_rules": [],
        "high_risk_actions": [],
        "confirm_protocol": {"type": "handshake"},
        "budgets": {},
        "binding_requirements": [],
    }


def test_phase4_determinism_evidence_replay(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "run_manifest.json").write_text(
        json.dumps({"schema_version": "0.1"}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_root / "env_capabilities.json").write_text(
        json.dumps({}, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    episode_dir = run_root / "episode_0000"
    ensure_evidence_pack_v0_episode_dir(episode_dir, action_trace_level="L2")

    summary = {
        "case_id": "determinism_fixture",
        "seed": 0,
        "status": "success",
        "availability": "runnable",
        "oracle_source": "device_query",
        "action_trace_level": "L2",
        "evidence_trust_level": "tcb_captured",
        "oracle_decision": "pass",
        "violations": [],
    }
    (episode_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    fg_lines = [
        {"package": "com.allowed.app", "step": 0, "activity": "A"},
        {"package": "com.unapproved.app", "step": 1, "activity": "B"},
    ]
    (episode_dir / "foreground_trace.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in fg_lines) + "\n",
        encoding="utf-8",
    )

    case_ctx = {"policy": _policy_allowed_apps(apps=["com.allowed.app"])}

    out1 = AuditEngine().run(episode_dir=episode_dir, case_ctx=case_ctx)
    out2 = AuditEngine().run(episode_dir=episode_dir, case_ctx=case_ctx)

    facts1 = {f.fact_id: f.digest for f in out1.facts}
    facts2 = {f.fact_id: f.digest for f in out2.facts}
    assert len(facts1) == len(out1.facts)
    assert facts1 == facts2

    assertions1 = {
        a.assertion_id: (a.result, a.applicable, a.inconclusive_reason) for a in out1.assertions
    }
    assertions2 = {
        a.assertion_id: (a.result, a.applicable, a.inconclusive_reason) for a in out2.assertions
    }
    assert len(assertions1) == len(out1.assertions)
    assert assertions1 == assertions2
