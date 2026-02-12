from __future__ import annotations

import json
from pathlib import Path

from mas_harness.integration.agents.registry import (
    load_agent_registry,
    validate_registry_manifest_consistency,
)


def test_repo_registry_manifest_consistency_passes() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    registry_path = repo_root / "mas-agents" / "registry" / "agent_registry.yaml"
    entries = load_agent_registry(registry_path)
    report = validate_registry_manifest_consistency(entries, repo_root=repo_root)
    assert report.errors == []


def test_detects_action_trace_level_mismatch(tmp_path: Path) -> None:
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    adapter_path = adapter_dir / "adapter.py"
    adapter_path.write_text("# dummy\n", encoding="utf-8")

    (adapter_dir / "adapter_manifest.json").write_text(
        json.dumps(
            {
                "agent_id": "mismatch_agent",
                "default_coord_space": "physical_px",
                "action_evidence": {
                    "level": "L2",
                    "source": "comm_proxy",
                    "event_stream": {
                        "format": "comm_proxy_trace_v1",
                        "path_on_host": "trace.jsonl",
                    },
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    entries = [
        {
            "agent_id": "mismatch_agent",
            "availability": "runnable",
            "action_trace_level": "L1",
            "adapter": str(adapter_path),
        }
    ]
    report = validate_registry_manifest_consistency(entries, repo_root=tmp_path)
    assert any(issue.code == "registry_manifest.level_mismatch" for issue in report.errors)
