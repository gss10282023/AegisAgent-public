from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from mas_harness.integration.agents.registry import default_env_profiles_dir, load_env_profile
from mas_harness.phases.phase0_artifacts import Phase0Config, ensure_phase0_artifacts


def test_can_load_env_profiles_from_repo_tree() -> None:
    env_profiles_dir = default_env_profiles_dir()

    mas_core = load_env_profile(env_profiles_dir, "mas_core")
    assert mas_core["id"] == "mas_core"

    aw = load_env_profile(env_profiles_dir, "android_world_compat")
    assert aw["id"] == "android_world_compat"


def test_validate_registry_cli_can_print_env_profile(capsys) -> None:
    from mas_harness.cli import validate_registry

    old_argv = sys.argv
    try:
        sys.argv = [
            "validate_registry",
            "--print_env_profile",
            "mas_core",
        ]
        assert validate_registry.main() == 0
    finally:
        sys.argv = old_argv

    out = capsys.readouterr().out.strip()
    data = yaml.safe_load(out)
    assert data["id"] == "mas_core"


def test_run_manifest_includes_env_profile(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]

    ensure_phase0_artifacts(
        out_dir=tmp_path,
        repo_root=repo_root,
        cfg=Phase0Config(env_profile="android_world_compat"),
        seed=0,
    )
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["env_profile"] == "android_world_compat"
