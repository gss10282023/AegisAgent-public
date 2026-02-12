from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas_harness.evidence.evidence_pack import ensure_evidence_pack_v0_episode_dir
from mas_harness.tools.audit_bundle import audit_bundle


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    _write_jsonl(
        evidence_dir / "ui_elements.jsonl",
        [
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
            }
        ],
    )


def _write_minimal_run_root(*, root: Path, action_trace_level: str) -> Path:
    _write_json(
        root / "run_manifest.json",
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
            "action_trace_level": action_trace_level,
        },
    )
    _write_json(
        root / "env_capabilities.json",
        {
            "schema_version": "0.1",
            "created_ts_ms": 0,
            "host": {},
            "repo": {},
            "android": {},
            "host_artifacts": {},
        },
    )

    evidence_dir = root / "episode_0000" / "evidence"
    _write_minimal_episode_bundle(evidence_dir=evidence_dir)
    return evidence_dir


@pytest.mark.parametrize("level", ["L0", "L1", "L2"])
def test_audit_bundle_requires_device_input_trace_for_l0_l1_l2(
    tmp_path: Path,
    level: str,
) -> None:
    _write_minimal_run_root(root=tmp_path, action_trace_level=level)

    # Do NOT create device_input_trace.jsonl.
    # The run_manifest declares L0/L1/L2, so this is an audit error.
    errors = audit_bundle(tmp_path)
    assert any("device_input_trace.jsonl" in e for e in errors), errors


def test_audit_bundle_fails_when_device_input_trace_empty(tmp_path: Path) -> None:
    evidence_dir = _write_minimal_run_root(root=tmp_path, action_trace_level="L0")
    (evidence_dir / "device_input_trace.jsonl").write_text("", encoding="utf-8")

    errors = audit_bundle(tmp_path)
    assert any("empty device_input_trace.jsonl" in e for e in errors), errors


def test_audit_bundle_fails_when_device_input_trace_source_level_mismatches_manifest(
    tmp_path: Path,
) -> None:
    evidence_dir = _write_minimal_run_root(root=tmp_path, action_trace_level="L1")

    _write_jsonl(
        evidence_dir / "device_input_trace.jsonl",
        [
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L2",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 10, "y": 10},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            }
        ],
    )

    errors = audit_bundle(tmp_path)
    assert any("source_level mismatch" in e for e in errors), errors


def test_audit_bundle_fails_when_device_input_trace_schema_invalid(tmp_path: Path) -> None:
    evidence_dir = _write_minimal_run_root(root=tmp_path, action_trace_level="L1")

    # Missing mapping_warnings (required by schema).
    _write_jsonl(
        evidence_dir / "device_input_trace.jsonl",
        [
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L1",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 10, "y": 10},
                "timestamp_ms": 1,
            }
        ],
    )

    errors = audit_bundle(tmp_path)
    assert any("required property" in e and "mapping_warnings" in e for e in errors), errors


def test_audit_bundle_l0_enforces_ref_step_idx_alignment(tmp_path: Path) -> None:
    evidence_dir = _write_minimal_run_root(root=tmp_path, action_trace_level="L0")

    _write_jsonl(
        evidence_dir / "device_input_trace.jsonl",
        [
            {
                "step_idx": 0,
                "ref_step_idx": 1,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 10, "y": 10},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            }
        ],
    )

    errors = audit_bundle(tmp_path)
    assert any("ref_step_idx must equal step_idx for L0" in e for e in errors), errors


def test_audit_bundle_l0_enforces_strict_coords(tmp_path: Path) -> None:
    evidence_dir = _write_minimal_run_root(root=tmp_path, action_trace_level="L0")

    _write_jsonl(
        evidence_dir / "device_input_trace.jsonl",
        [
            {
                "step_idx": 0,
                "ref_step_idx": 0,
                "source_level": "L0",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": None, "y": None},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            }
        ],
    )

    errors = audit_bundle(tmp_path)
    assert any("L0 coordinate events require resolved x/y" in e for e in errors), errors


def test_audit_bundle_l1_l2_enforces_step_idx_monotonic_unique(tmp_path: Path) -> None:
    evidence_dir = _write_minimal_run_root(root=tmp_path, action_trace_level="L2")

    _write_jsonl(
        evidence_dir / "device_input_trace.jsonl",
        [
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L2",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 10, "y": 10},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            },
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L2",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 11, "y": 11},
                "timestamp_ms": 2,
                "mapping_warnings": [],
            },
        ],
    )

    errors = audit_bundle(tmp_path)
    assert any("step_idx must be strictly increasing" in e for e in errors), errors


def test_audit_bundle_l1_coords_missing_requires_coord_unresolved_warning(tmp_path: Path) -> None:
    evidence_dir = _write_minimal_run_root(root=tmp_path, action_trace_level="L1")

    _write_jsonl(
        evidence_dir / "device_input_trace.jsonl",
        [
            {
                "step_idx": 0,
                "ref_step_idx": None,
                "source_level": "L1",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": None, "y": None},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            }
        ],
    )

    errors = audit_bundle(tmp_path)
    assert any("coord_unresolved" in e for e in errors), errors


def test_audit_bundle_allows_l2_ref_step_idx_missing_or_duplicate(tmp_path: Path) -> None:
    evidence_dir = _write_minimal_run_root(root=tmp_path, action_trace_level="L2")

    _write_jsonl(
        evidence_dir / "device_input_trace.jsonl",
        [
            {
                "step_idx": 0,
                # ref_step_idx intentionally omitted (allowed for L2).
                "source_level": "L2",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 10, "y": 10},
                "timestamp_ms": 1,
                "mapping_warnings": [],
            },
            {
                "step_idx": 1,
                "ref_step_idx": 0,
                "source_level": "L2",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 11, "y": 11},
                "timestamp_ms": 2,
                "mapping_warnings": [],
            },
            {
                "step_idx": 2,
                "ref_step_idx": 0,
                "source_level": "L2",
                "event_type": "tap",
                "payload": {"coord_space": "physical_px", "x": 12, "y": 12},
                "timestamp_ms": 3,
                "mapping_warnings": [],
            },
        ],
    )

    errors = audit_bundle(tmp_path)
    assert not errors, errors
