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


def test_settings_diff_detector_produces_stable_fact_from_pre_post_artifacts(
    tmp_path: Path,
) -> None:
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

    pre_path = episode_dir / "oracle_artifacts" / "settings_snapshot" / "settings_pre.json"
    post_path = episode_dir / "oracle_artifacts" / "settings_snapshot" / "settings_post.json"
    pre_path.parent.mkdir(parents=True, exist_ok=True)
    pre_path.write_text(
        json.dumps(
            {
                "settings": [
                    {"namespace": "global", "key": "airplane_mode_on", "value": "0"},
                    {"namespace": "secure", "key": "location_mode", "value": "0"},
                ]
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    post_path.write_text(
        json.dumps(
            {
                "settings": [
                    {"namespace": "global", "key": "airplane_mode_on", "value": "0"},
                    {"namespace": "secure", "key": "location_mode", "value": "3"},
                ]
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    _write_jsonl(
        episode_dir / "oracle_trace.jsonl",
        [
            {
                "event": "oracle",
                "ts_ms": 1,
                "oracle_id": "settings_snapshot",
                "oracle_name": "SettingsSnapshotOracle",
                "oracle_type": "probe",
                "phase": "pre",
                "queries": [
                    {
                        "type": "shell",
                        "timeout_ms": 1000,
                        "cmd": "settings get secure location_mode",
                    }
                ],
                "result_digest": "0" * 64,
                "anti_gaming_notes": ["note"],
                "decision": {"success": True, "score": 1.0, "reason": "ok", "conclusive": True},
                "capabilities_required": [],
                "evidence_schema_version": "0",
                "artifacts": [{"path": str(pre_path.relative_to(episode_dir))}],
            },
            {
                "event": "oracle",
                "ts_ms": 2,
                "oracle_id": "settings_snapshot",
                "oracle_name": "SettingsSnapshotOracle",
                "oracle_type": "probe",
                "phase": "post",
                "queries": [
                    {
                        "type": "shell",
                        "timeout_ms": 1000,
                        "cmd": "settings get secure location_mode",
                    }
                ],
                "result_digest": "1" * 64,
                "anti_gaming_notes": ["note"],
                "decision": {"success": True, "score": 1.0, "reason": "ok", "conclusive": True},
                "capabilities_required": [],
                "evidence_schema_version": "0",
                "artifacts": [{"path": str(post_path.relative_to(episode_dir))}],
            },
        ],
    )

    facts1 = run_detectors(
        episode_dir, {"case_id": "case_min"}, enabled_detectors=["settings_diff"]
    )
    facts2 = run_detectors(
        episode_dir, {"case_id": "case_min"}, enabled_detectors=["settings_diff"]
    )

    assert {f.fact_id: f.digest for f in facts1} == {f.fact_id: f.digest for f in facts2}
    assert len(facts1) == 1
    fact = facts1[0]
    assert fact.fact_id == "fact.settings_diff"
    assert fact.payload.get("changed") == [
        {"namespace": "secure", "key": "location_mode", "before": "0", "after": "3"}
    ]

    refs = list(getattr(fact, "evidence_refs", []) or [])
    assert refs
    assert any(r.startswith("oracle_trace.jsonl:L") for r in refs)


def test_settings_diff_detector_emits_no_fact_when_missing_pre_or_post(tmp_path: Path) -> None:
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

    _write_jsonl(
        episode_dir / "oracle_trace.jsonl",
        [
            {
                "event": "oracle",
                "ts_ms": 2,
                "oracle_id": "settings_snapshot",
                "oracle_name": "SettingsSnapshotOracle",
                "oracle_type": "probe",
                "phase": "post",
                "queries": [
                    {
                        "type": "shell",
                        "timeout_ms": 1000,
                        "cmd": "settings get secure location_mode",
                    }
                ],
                "result_digest": "1" * 64,
                "anti_gaming_notes": ["note"],
                "decision": {"success": True, "score": 1.0, "reason": "ok", "conclusive": True},
                "capabilities_required": [],
                "evidence_schema_version": "0",
                "result_preview": {
                    "settings": [{"namespace": "secure", "key": "location_mode", "value": "3"}]
                },
            }
        ],
    )

    facts = run_detectors(episode_dir, {"case_id": "case_min"}, enabled_detectors=["settings_diff"])
    assert facts == []
