from __future__ import annotations

from pathlib import Path

from mas_harness.phases.phase0_artifacts import Phase0Config, probe_env_capabilities


def test_probe_env_capabilities_includes_standard_fields(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[4]

    artifacts_root = tmp_path / "artifacts_root"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    caps = probe_env_capabilities(repo_root=repo_root, cfg=Phase0Config(android_serial=None))
    assert caps["schema_version"] == "0.2"
    assert "capabilities" in caps

    std = caps["capabilities"]
    for key in (
        "root_available",
        "run_as_available",
        "can_pull_data",
        "sdcard_writable",
        "host_artifacts_available",
        "android_api_level",
    ):
        assert key in std

    assert std["host_artifacts_available"] is True
    assert caps["host_artifacts"]["host_artifacts_available"] is True

    assert "android_api_level" in caps["android"]
    assert "can_pull_data" in caps["android"]
