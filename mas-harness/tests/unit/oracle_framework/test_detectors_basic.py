from __future__ import annotations

import json
from pathlib import Path

from mas_harness.evidence.evidence_pack import ensure_evidence_pack_v0_episode_dir
from mas_harness.oracle_framework.engine import run_detectors
from mas_harness.oracle_framework.schema_validators import assert_fact_v0


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


def test_detector_engine_writes_stable_facts(tmp_path: Path) -> None:
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
            "steps": 2,
            "duration_ms": 123,
            "env_profile": "mas_core",
            "evidence_trust_level": "tcb_captured",
            "oracle_source": "device_query",
        },
    )

    _write_jsonl(
        episode_dir / "foreground_trace.jsonl",
        [
            {
                "event": "foreground",
                "ts_ms": 1,
                "step": 0,
                "package": "com.example.app",
                "activity": "Main",
            },
            {
                "event": "foreground",
                "ts_ms": 2,
                "step": 1,
                "package": "com.example.other",
                "activity": "Other",
            },
        ],
    )

    _write_jsonl(
        episode_dir / "device_input_trace.jsonl",
        [
            {
                "step_idx": 0,
                "ref_step_idx": 0,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 1, "y": 2},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            }
        ],
    )

    _write_jsonl(
        episode_dir / "oracle_trace.jsonl",
        [
            {
                "event": "oracle",
                "ts_ms": 3,
                "oracle_id": "test_oracle",
                "oracle_name": "TestOracle",
                "oracle_type": "hard",
                "phase": "post",
                "queries": [{"type": "shell", "timeout_ms": 1000, "cmd": "echo ok"}],
                "result_digest": "0" * 64,
                "anti_gaming_notes": ["note"],
                "decision": {"success": True, "score": 1.0, "reason": "ok", "conclusive": True},
                "capabilities_required": [],
                "evidence_schema_version": "0",
                "artifacts": [
                    {
                        "path": "artifacts/test.txt",
                        "sha256": "1" * 64,
                        "bytes": 2,
                        "mime": "text/plain",
                    }
                ],
                "result_preview": {"ok": True},
            }
        ],
    )

    facts1 = run_detectors(episode_dir, {"case_id": "case_min"})
    facts2 = run_detectors(episode_dir, {"case_id": "case_min"})

    digests1 = {f.fact_id: f.digest for f in facts1}
    digests2 = {f.fact_id: f.digest for f in facts2}
    assert digests1 == digests2

    fact_ids = set(digests1.keys())
    assert "fact.foreground_pkg_seq" in fact_ids
    assert "fact.step_count" in fact_ids
    assert any(fid.startswith("fact.oracle_event_index/") for fid in fact_ids)

    for f in facts1:
        obj = f.to_dict()
        assert_fact_v0(obj)
        assert obj["evidence_refs"], f"expected non-empty evidence_refs for {f.fact_id}"
