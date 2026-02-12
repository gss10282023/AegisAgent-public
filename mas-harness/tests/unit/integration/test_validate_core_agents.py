from __future__ import annotations

from pathlib import Path

from mas_harness.integration.agents.registry import load_agent_registry, validate_core_agents


def _write_dummy_adapter(tmp_path: Path) -> Path:
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    adapter_path = adapter_dir / "adapter.py"
    adapter_path.write_text("# dummy\n", encoding="utf-8")
    (adapter_dir / "adapter_manifest.json").write_text("{}", encoding="utf-8")
    return adapter_path


def test_repo_core_agents_validation_passes() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    registry_path = repo_root / "mas-agents" / "registry" / "agent_registry.yaml"
    entries = load_agent_registry(registry_path)
    report = validate_core_agents(entries, repo_root=repo_root)
    assert report.errors == []


def test_core_requires_action_trace_level_l0_l2(tmp_path: Path) -> None:
    adapter_path = _write_dummy_adapter(tmp_path)
    entries = [
        {
            "agent_id": "core_agent",
            "tier": "core",
            "availability": "runnable",
            "action_trace_level": "none",
            "adapter": str(adapter_path),
        }
    ]
    report = validate_core_agents(entries, repo_root=tmp_path)
    assert any(issue.code == "core_agents.invalid_action_trace_level" for issue in report.errors)


def test_core_requires_runnable_availability(tmp_path: Path) -> None:
    adapter_path = _write_dummy_adapter(tmp_path)
    entries = [
        {
            "agent_id": "core_agent",
            "tier": "core",
            "availability": "audit_only",
            "action_trace_level": "L0",
            "adapter": str(adapter_path),
        }
    ]
    report = validate_core_agents(entries, repo_root=tmp_path)
    assert any(issue.code == "core_agents.not_runnable" for issue in report.errors)


def test_entries_require_tier_field(tmp_path: Path) -> None:
    adapter_path = _write_dummy_adapter(tmp_path)
    entries = [
        {
            "agent_id": "missing_tier",
            "availability": "runnable",
            "action_trace_level": "L0",
            "adapter": str(adapter_path),
        }
    ]
    report = validate_core_agents(entries, repo_root=tmp_path)
    assert any(issue.code == "core_agents.missing_tier" for issue in report.errors)
