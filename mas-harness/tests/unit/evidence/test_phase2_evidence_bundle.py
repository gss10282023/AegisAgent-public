from __future__ import annotations

import json
from pathlib import Path

from mas_harness.evidence.action_normalizer import validate_mas_action
from mas_harness.evidence.evidence_pack import (
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS,
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON,
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSONL,
    EVIDENCE_PACK_V0_RUN_REQUIRED_FILES,
)
from mas_harness.runtime.run_public import run_case
from mas_harness.tools.audit_bundle import audit_bundle


def test_phase2_bundle_has_required_files(tmp_path: Path) -> None:
    # Locate the repo root (…/mas-harness/tests/ -> repo root)
    repo_root = Path(__file__).resolve().parents[4]
    case_dir = repo_root / "mas-public" / "cases" / "smoke_001"

    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=repo_root / "mas-spec" / "schemas",
    )

    # Phase-0 governance artifacts (run-level)
    for name in EVIDENCE_PACK_V0_RUN_REQUIRED_FILES:
        assert (tmp_path / name).exists(), f"missing run-level file: {name}"
    case_id = summary["case_id"]

    run_dir = tmp_path / "public" / case_id / "seed_0"
    assert run_dir.is_dir(), f"missing run_dir: {run_dir}"

    for name in EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSONL:
        assert (run_dir / name).exists(), f"missing episode file: {name}"
    for name in EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON:
        assert (run_dir / name).exists(), f"missing episode file: {name}"
    for name in EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS:
        assert (run_dir / name).is_dir(), f"missing episode dir: {name}"

    # Observation artifacts
    screenshots_dir = run_dir / "screenshots"
    ui_dump_dir = run_dir / "ui_dump"
    assert any(
        p.name.startswith("screenshot_") for p in screenshots_dir.iterdir()
    ), "no screenshots saved"
    assert any(p.name.startswith("a11y_") for p in ui_dump_dir.iterdir()), "no a11y dumps saved"
    assert any(
        p.name.startswith("uiautomator_") and p.suffix == ".xml" for p in ui_dump_dir.iterdir()
    ), "no uiautomator xml dumps saved"

    # Summary must include oracle-derived success
    with (run_dir / "summary.json").open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert "task_success" in loaded
    assert loaded["task_success"] in {True, False, "unknown"}
    assert "task_success_details" in loaded
    assert "score" in loaded["task_success_details"]
    assert "oracle_id" in loaded["task_success_details"]

    # Step 14: agent_action_trace must include schema-valid normalized_action.
    agent_action_events = []
    with (run_dir / "agent_action_trace.jsonl").open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("event") == "agent_action":
                agent_action_events.append(obj)
                validate_mas_action(obj["normalized_action"])
    assert agent_action_events, "missing agent_action events"

    # Bundle auditor must pass.
    errors = audit_bundle(tmp_path)
    assert not errors, "bundle audit errors:\n" + "\n".join(errors)
