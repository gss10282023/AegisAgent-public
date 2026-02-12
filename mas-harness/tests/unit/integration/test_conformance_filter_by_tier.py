from __future__ import annotations

from pathlib import Path

from mas_harness.integration.conformance import suite


def test_conformance_core_batch_run_filters_by_tier(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MAS_ANDROID_SERIAL", raising=False)

    repo_root = Path(__file__).resolve().parents[4]
    cases_dir = repo_root / "mas-conformance" / "cases"
    registry_path = repo_root / "mas-agents" / "registry" / "agent_registry.yaml"
    output_dir = tmp_path / "conformance_core"

    rc = suite.main(
        [
            "--cases",
            str(cases_dir),
            "--agents_from_registry",
            str(registry_path),
            "--filter",
            "availability=runnable,tier=core",
            "--output",
            str(output_dir),
        ]
    )
    assert rc == 0

    expected_agents = {
        "autoglm-mobile",
        "droidrun",
        "minitap-mobile-use",
        "toy_agent",
        "toy_agent_driven_l1",
        "toy_agent_driven_l2",
        "ui-tars-7b",
    }
    agent_dirs = {p.name for p in output_dir.iterdir() if p.is_dir()}
    assert agent_dirs == expected_agents

    for agent_id in expected_agents:
        run_root = output_dir / agent_id
        assert (
            run_root / "run_manifest.json"
        ).exists(), f"missing run_manifest.json under {run_root}"
        assert (
            run_root / "env_capabilities.json"
        ).exists(), f"missing env_capabilities.json under {run_root}"
        episodes = sorted(
            p for p in run_root.iterdir() if p.is_dir() and p.name.startswith("episode_")
        )
        assert episodes, f"missing episode_* dirs under: {run_root}"
