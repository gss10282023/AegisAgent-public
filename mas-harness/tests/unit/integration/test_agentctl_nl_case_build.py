from __future__ import annotations

import json
import sys
from pathlib import Path

from mas_harness.phases.phase3_smoke_cases import build_nl_smoke_case, write_case_dir
from mas_harness.spec.validate_case import load_and_validate_case


def _run_cli(argv: list[str]) -> int:
    from mas_harness.cli import agentctl

    old_argv = sys.argv
    try:
        sys.argv = argv
        return int(agentctl.main())
    finally:
        sys.argv = old_argv


def test_agentctl_nl_case_build_validates_against_schema(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    case_dir = tmp_path / "nl_case"
    specs = build_nl_smoke_case(goal="打开设置", max_steps=20)
    write_case_dir(case_dir=case_dir, specs=specs)

    loaded = load_and_validate_case(case_dir=case_dir, schemas_dir=schemas_dir)
    assert loaded.task["user_goal"] == "打开设置"
    assert loaded.task["max_steps"] == 20
    assert loaded.task["success_oracle"]["type"] == "none"


def test_agentctl_nl_cli_writes_manifest_with_agentctl_purpose(tmp_path: Path) -> None:
    out_dir = tmp_path / "agentctl_nl_run"
    rc = _run_cli(
        [
            "agentctl",
            "nl",
            "--agent_id",
            "toy_agent",
            "--goal",
            "打开设置并进入 Wi-Fi 页面",
            "--max_steps",
            "5",
            "--output",
            str(out_dir),
        ]
    )
    assert rc == 0

    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_purpose"] == "agentctl_nl"
    assert manifest["oracle_source"] == "none"
