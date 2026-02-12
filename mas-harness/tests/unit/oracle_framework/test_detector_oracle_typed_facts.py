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


def test_detector_oracle_typed_facts_produces_stable_semantic_facts(tmp_path: Path) -> None:
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
                    "matches": [{"address": "+1-555-000", "body_preview": "SMS_TOKEN_1 hello"}],
                    "box": "sent",
                },
            },
            {
                "event": "oracle",
                "ts_ms": 2,
                "oracle_id": "sqlite_pull_query",
                "oracle_name": "sqlite_pull_query",
                "oracle_type": "hard",
                "phase": "post",
                "queries": [
                    {"type": "file_pull", "timeout_ms": 1000, "path": "/sdcard/db.sqlite"},
                    {
                        "type": "sqlite",
                        "timeout_ms": 0,
                        "path": "/sdcard/db.sqlite",
                        "sql": "select 1",
                    },
                ],
                "result_digest": "1" * 64,
                "anti_gaming_notes": ["note"],
                "decision": {"success": True, "score": 1.0, "reason": "ok", "conclusive": True},
                "capabilities_required": [],
                "evidence_schema_version": "0",
                "artifacts": [
                    {
                        "path": "oracle_artifacts/sqlite_pull_query/db.sqlite",
                        "sha256": "2" * 64,
                        "bytes": 123,
                        "mime": "application/x-sqlite3",
                    }
                ],
                "result_preview": {"row_count": 1, "sample_rows": [{"token": "SECRET"}]},
            },
        ],
    )

    ctx = {"case_id": "case_min", "success_oracle_name": "sms_provider"}
    facts1 = run_detectors(episode_dir, ctx, enabled_detectors=["oracle_typed_facts"])
    facts2 = run_detectors(episode_dir, ctx, enabled_detectors=["oracle_typed_facts"])

    digests1 = {f.fact_id: f.digest for f in facts1}
    digests2 = {f.fact_id: f.digest for f in facts2}
    assert digests1 == digests2

    fact_ids = set(digests1.keys())
    assert "fact.task.success_oracle_decision" in fact_ids
    assert any(fid.startswith("fact.provider.sms_activity_summary/") for fid in fact_ids)
    assert any(fid.startswith("fact.sqlite.query_result_summary/") for fid in fact_ids)

    for f in facts1:
        if not (
            f.fact_id.startswith("fact.oracle.")
            or f.fact_id.startswith("fact.provider.")
            or f.fact_id.startswith("fact.sqlite.")
            or f.fact_id.startswith("fact.task.")
        ):
            continue
        refs = list(getattr(f, "evidence_refs", []) or [])
        assert any(
            r.startswith("oracle_trace.jsonl:L") for r in refs
        ), f"missing line-level evidence_ref: {f.fact_id}"
