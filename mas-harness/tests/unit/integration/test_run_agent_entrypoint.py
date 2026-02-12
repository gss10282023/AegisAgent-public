from __future__ import annotations

import json
import sys
from pathlib import Path

from mas_harness.evidence.evidence_pack import (
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS,
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON,
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSONL,
    EVIDENCE_PACK_V0_RUN_REQUIRED_FILES,
)
from mas_harness.tools.audit_bundle import audit_bundle


def _run_cli(argv: list[str]) -> int:
    from mas_harness.cli import run_agent

    old_argv = sys.argv
    try:
        sys.argv = argv
        return int(run_agent.main())
    finally:
        sys.argv = old_argv


def _assert_episode_layout(run_root: Path) -> None:
    episodes = sorted(p for p in run_root.iterdir() if p.is_dir() and p.name.startswith("episode_"))
    assert episodes, f"missing episode_* dirs under: {run_root}"

    for episode_dir in episodes:
        assert (episode_dir / "summary.json").exists(), f"missing episode summary: {episode_dir}"
        evidence_dir = episode_dir / "evidence"
        assert evidence_dir.is_dir(), f"missing evidence dir: {evidence_dir}"

        for name in EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSONL:
            assert (evidence_dir / name).exists(), f"missing evidence file: {evidence_dir / name}"
        for name in EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON:
            assert (evidence_dir / name).exists(), f"missing evidence file: {evidence_dir / name}"
        for name in EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS:
            assert (evidence_dir / name).is_dir(), f"missing evidence dir: {evidence_dir / name}"


def test_run_agent_runnable_toy_agent(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    case_dir = repo_root / "mas-public" / "cases" / "smoke_001"
    out_dir = tmp_path / "phase3_smoke_toy"

    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "toy_agent",
            "--case_dir",
            str(case_dir),
            "--output",
            str(out_dir),
        ]
    )
    assert rc == 0

    for name in EVIDENCE_PACK_V0_RUN_REQUIRED_FILES:
        assert (out_dir / name).exists(), f"missing run-level file: {out_dir / name}"

    _assert_episode_layout(out_dir)
    assert audit_bundle(out_dir) == []


def test_run_agent_audit_only_ingest(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    traj = repo_root / "mas-harness" / "tests" / "fixtures" / "aw_traj_sample.jsonl"
    out_dir = tmp_path / "phase3_ingest_sample"

    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "some_audit_only_agent",
            "--trajectory",
            str(traj),
            "--output",
            str(out_dir),
        ]
    )
    assert rc == 0

    for name in EVIDENCE_PACK_V0_RUN_REQUIRED_FILES:
        assert (out_dir / name).exists(), f"missing run-level file: {out_dir / name}"

    _assert_episode_layout(out_dir)
    assert audit_bundle(out_dir) == []

    evidence_summary = json.loads(
        (out_dir / "episode_0000" / "evidence" / "summary.json").read_text(encoding="utf-8")
    )
    assert evidence_summary["run_mode"] == "audit_only"
