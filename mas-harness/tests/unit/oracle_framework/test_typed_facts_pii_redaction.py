from __future__ import annotations

import json
from pathlib import Path

from mas_harness.evidence.evidence_pack import ensure_evidence_pack_v0_episode_dir
from mas_harness.oracle_framework.engine import run_detectors


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = "\n".join(
        json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for line in lines
    )
    path.write_text(raw + ("\n" if raw else ""), encoding="utf-8")


def test_typed_facts_sms_provider_redacts_pii(tmp_path: Path) -> None:
    phone = "+1-555-000"
    msg = "SMS_TOKEN_1 hello there"

    run_root = tmp_path / "run"
    _write_json(
        run_root / "run_manifest.json",
        {
            "schema_version": "0.1",
            "env_profile": "mas_core",
            "evidence_trust_level": "tcb_captured",
            "oracle_source": "device_query",
            "action_trace_level": "L0",
            "action_trace_source": "mas_executor",
        },
    )
    _write_json(run_root / "env_capabilities.json", {})

    episode_dir = run_root / "public" / "case_min" / "seed_0"
    ensure_evidence_pack_v0_episode_dir(episode_dir, action_trace_level="L0")
    _write_json(
        episode_dir / "summary.json",
        {
            "case_id": "case_min",
            "seed": 0,
            "steps": 1,
            "duration_ms": 1,
            "env_profile": "mas_core",
            "evidence_trust_level": "tcb_captured",
            "oracle_source": "device_query",
        },
    )

    _write_jsonl(
        episode_dir / "oracle_trace.jsonl",
        [
            {
                "event": "oracle",
                "ts_ms": 1,
                "oracle_id": "sms_provider",
                "oracle_name": "sms_provider",
                "oracle_type": "hard",
                "phase": "post",
                "queries": [
                    {"type": "content_query", "timeout_ms": 1000, "uri": "content://sms/sent"}
                ],
                "result_digest": "0" * 64,
                "anti_gaming_notes": ["note"],
                "decision": {"success": True, "score": 1.0, "reason": "ok", "conclusive": True},
                "capabilities_required": [],
                "evidence_schema_version": "0",
                "result_preview": {
                    "matched": True,
                    "match_count": 1,
                    "matches": [{"address": phone, "body_preview": msg}],
                    "box": "sent",
                },
            }
        ],
    )

    facts = run_detectors(
        episode_dir,
        {"success_oracle_name": "sms_provider"},
        enabled_detectors=["oracle_typed_facts"],
    )

    sms_facts = [f for f in facts if f.fact_id.startswith("fact.provider.sms_activity_summary/")]
    assert len(sms_facts) == 1
    payload_json = json.dumps(
        dict(sms_facts[0].payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )

    assert phone not in payload_json
    assert msg not in payload_json
    assert "body_preview" not in payload_json
    assert "address" not in payload_json
