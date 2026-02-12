from __future__ import annotations

import json
from pathlib import Path

from mas_harness.evidence.evidence_pack import ensure_evidence_pack_v0_episode_dir
from mas_harness.tools.audit_bundle import audit_bundle


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _write_minimal_episode_bundle(*, evidence_dir: Path) -> None:
    ensure_evidence_pack_v0_episode_dir(evidence_dir)

    # Satisfy audit_bundle's Step 7 UIAutomator XML requirement.
    uia = evidence_dir / "ui_dump" / "uiautomator_0000.xml"
    uia.write_text(
        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
        '<hierarchy rotation="0"></hierarchy>\n',
        encoding="utf-8",
    )

    # Satisfy summary.json minimum keys.
    _write_json(
        evidence_dir / "summary.json",
        {
            "case_id": "case_0",
            "seed": 0,
            "status": "inconclusive",
            "task_success": {
                "success": False,
                "conclusive": False,
                "score": 0.0,
                "reason": "unit_test",
            },
        },
    )

    # Satisfy ui_elements.jsonl minimum event requirements.
    ui_elements_path = evidence_dir / "ui_elements.jsonl"
    ui_elements_path.write_text(
        json.dumps(
            {
                "event": "ui_elements",
                "ts_ms": 1,
                "step": 0,
                "ui_hash": "unit_test_ui_hash",
                "source": "unit_test",
                "ui_elements": [
                    {
                        "bbox": [0, 0, 10, 10],
                        "package": "com.example",
                        "resource_id": "id/ok",
                        "text": "ok",
                        "desc": None,
                        "clickable": True,
                    }
                ],
                "elements_count": 1,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_evidence_pack_requires_device_input_trace_when_l0(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L0",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    # Do NOT create device_input_trace.jsonl. L0 must require it.
    errors = audit_bundle(tmp_path)
    assert any("device_input_trace.jsonl" in e for e in errors), errors


def test_evidence_pack_requires_device_input_trace_when_l1(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L1",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    errors = audit_bundle(tmp_path)
    assert any("device_input_trace.jsonl" in e for e in errors), errors


def test_evidence_pack_requires_device_input_trace_when_l2(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L2",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    errors = audit_bundle(tmp_path)
    assert any("device_input_trace.jsonl" in e for e in errors), errors


def test_evidence_pack_does_not_require_device_input_trace_when_none(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "none",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    errors = audit_bundle(tmp_path)
    assert not errors, errors


def test_evidence_pack_fails_when_device_input_trace_level_mismatches_manifest(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L1",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    # Present but invalid: file is L2 while manifest expects L1.
    (evidence_dir / "device_input_trace.jsonl").write_text(
        json.dumps(
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L2",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 10, "y": 10},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    errors = audit_bundle(tmp_path)
    assert any("source_level mismatch" in e for e in errors), errors


def test_evidence_pack_l0_enforces_ref_step_idx_alignment(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L0",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    (evidence_dir / "device_input_trace.jsonl").write_text(
        json.dumps(
            {
                "step_idx": 0,
                "ref_step_idx": 1,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 10, "y": 10},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    errors = audit_bundle(tmp_path)
    assert any("ref_step_idx must equal step_idx for L0" in e for e in errors), errors


def test_evidence_pack_l0_enforces_strict_coords(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L0",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    (evidence_dir / "device_input_trace.jsonl").write_text(
        json.dumps(
            {
                "step_idx": 0,
                "ref_step_idx": 0,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": None, "y": None},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    errors = audit_bundle(tmp_path)
    assert any("L0 coordinate events require resolved x/y" in e for e in errors), errors


def test_evidence_pack_l1_enforces_coord_unresolved_warning(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L1",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    (evidence_dir / "device_input_trace.jsonl").write_text(
        json.dumps(
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L1",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": None, "y": None},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    errors = audit_bundle(tmp_path)
    assert any("coord_unresolved" in e for e in errors), errors


def test_audit_bundle_accepts_valid_device_input_trace_when_l0(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L0",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    (evidence_dir / "device_input_trace.jsonl").write_text(
        json.dumps(
            {
                "step_idx": 0,
                "ref_step_idx": 0,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 10, "y": 10},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    errors = audit_bundle(tmp_path)
    assert not errors, errors


def test_audit_bundle_accepts_valid_device_input_trace_when_l2(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "execution_mode": "planner_only",
            "seed": 0,
            "agent": {},
            "inference": {},
            "reproducibility": {},
            "android": {},
            "llm_cache": {},
            "action_trace_level": "L2",
        },
    )
    _write_json(
        tmp_path / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = tmp_path / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)

    (evidence_dir / "device_input_trace.jsonl").write_text(
        json.dumps(
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L2",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": None, "y": None},
                "timestamp_ms": 1,
                "mapping_warnings": ["coord_unresolved"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    errors = audit_bundle(tmp_path)
    assert not errors, errors
