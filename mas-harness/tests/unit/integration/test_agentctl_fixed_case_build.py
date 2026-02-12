from __future__ import annotations

import json
import sys
from pathlib import Path

from mas_harness.phases.phase3_smoke_cases import (
    build_fixed_smoke_case_open_settings,
    write_case_dir,
)
from mas_harness.spec.validate_case import load_and_validate_case


def _run_cli(argv: list[str]) -> int:
    from mas_harness.cli import agentctl

    old_argv = sys.argv
    try:
        sys.argv = argv
        return int(agentctl.main())
    finally:
        sys.argv = old_argv


def test_agentctl_fixed_case_build_validates_against_schema(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    case_dir = tmp_path / "fixed_case"
    specs = build_fixed_smoke_case_open_settings()
    write_case_dir(case_dir=case_dir, specs=specs)

    loaded = load_and_validate_case(case_dir=case_dir, schemas_dir=schemas_dir)
    assert loaded.task["task_id"] == "agentctl_fixed_open_settings"
    assert loaded.task["success_oracle"]["type"] == "resumed_activity"


def test_agentctl_fixed_cli_writes_manifest_with_agentctl_purpose(tmp_path: Path) -> None:
    out_dir = tmp_path / "agentctl_fixed_run"
    rc = _run_cli(["agentctl", "fixed", "--agent_id", "toy_agent", "--output", str(out_dir)])
    assert rc == 0

    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_purpose"] == "agentctl_fixed"
    assert manifest["oracle_source"] == "device_query"
