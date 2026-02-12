"""Utilities for locating audit_only ingestion mapping notes (Phase3 3c-1f)."""

from __future__ import annotations

from pathlib import Path

from mas_harness.integration.agents.registry import discover_repo_root, load_agent_registry

FORMAT_ID_TO_NOTES_DIR: dict[str, str] = {
    "androidworld_jsonl": "androidworld",
}


def mapping_notes_path(format_id: str, *, repo_root: Path | None = None) -> Path:
    if not isinstance(format_id, str):
        raise ValueError("format_id must be a string")
    format_id = format_id.strip()
    if not format_id:
        raise ValueError("format_id must be a non-empty string")

    notes_dir = FORMAT_ID_TO_NOTES_DIR.get(format_id)
    if notes_dir is None:
        raise ValueError(f"unknown trajectory format_id for mapping notes: {format_id}")

    root = repo_root or discover_repo_root()
    return root / "mas-agents" / "ingest" / notes_dir / "mapping_notes.md"


def required_mapping_notes_paths(
    *, repo_root: Path | None = None, registry_path: Path | None = None
) -> dict[str, Path]:
    root = repo_root or discover_repo_root()
    registry = registry_path or (root / "mas-agents" / "registry" / "agent_registry.yaml")

    formats: set[str] = set()
    for entry in load_agent_registry(registry):
        availability = str(entry.get("availability") or "").strip()
        if availability != "audit_only":
            continue
        format_id = str(entry.get("trajectory_format") or "").strip()
        if format_id:
            formats.add(format_id)

    return {
        format_id: mapping_notes_path(format_id, repo_root=root) for format_id in sorted(formats)
    }
