from __future__ import annotations

from pathlib import Path

import pytest

from mas_harness.integration.ingestion.mapping_notes import required_mapping_notes_paths


def test_mapping_notes_exist_for_audit_only_formats() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    required = required_mapping_notes_paths(repo_root=repo_root)

    for format_id, path in required.items():
        assert path.exists(), f"missing mapping notes for {format_id}: {path}"
        text = path.read_text(encoding="utf-8").strip()
        assert format_id in text, f"mapping notes must mention format_id={format_id}: {path}"
        assert (
            len([line for line in text.splitlines() if line.strip()]) >= 10
        ), f"mapping notes too short for {format_id}: {path}"


def test_every_ingest_plugin_dir_has_mapping_notes() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    ingest_root = repo_root / "mas-agents" / "ingest"
    if not ingest_root.exists():
        pytest.skip(f"missing ingest dir: {ingest_root}")

    plugin_dirs = sorted(p for p in ingest_root.iterdir() if p.is_dir())
    for plugin_dir in plugin_dirs:
        notes = plugin_dir / "mapping_notes.md"
        assert notes.exists(), f"missing mapping_notes.md under: {plugin_dir}"
        assert notes.read_text(encoding="utf-8").strip(), f"empty mapping_notes.md: {notes}"
