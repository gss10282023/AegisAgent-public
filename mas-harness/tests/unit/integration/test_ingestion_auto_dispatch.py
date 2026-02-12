from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mas_harness.integration.ingestion import registry


class _RecordingIngestor:
    def __init__(self, format_id: str) -> None:
        self.format_id = format_id

    def probe(self, input_path: str) -> bool:
        del input_path
        return False

    def ingest(
        self,
        input_path: str,
        output_dir: str,
        *,
        agent_id: str | None = None,
        registry_entry: dict[str, Any] | None = None,
    ) -> None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "called.json").write_text(
            json.dumps(
                {
                    "input_path": input_path,
                    "agent_id": agent_id,
                    "registry_entry": registry_entry,
                    "format_id": self.format_id,
                },
                sort_keys=True,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


@pytest.fixture()
def _fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_REGISTRY", {})


def test_ingest_trajectory_cli_dispatch_by_format(tmp_path: Path, _fresh_registry: None) -> None:
    from mas_harness.cli.ingest_trajectory import main

    registry.register_ingestor(_RecordingIngestor("dummy_v1"))

    input_path = tmp_path / "traj.jsonl"
    input_path.write_text('{"hello":"world"}\n', encoding="utf-8")
    output_dir = tmp_path / "out"

    rc = main(["--format", "dummy_v1", "--input", str(input_path), "--output", str(output_dir)])
    assert rc == 0

    called = json.loads((output_dir / "called.json").read_text(encoding="utf-8"))
    assert called["format_id"] == "dummy_v1"
    assert called["input_path"] == str(input_path)
    assert called["agent_id"] is None
    assert called["registry_entry"] is None


def test_ingest_trajectory_cli_auto_dispatch_from_registry(
    tmp_path: Path, _fresh_registry: None
) -> None:
    from mas_harness.cli.ingest_trajectory import main

    registry.register_ingestor(_RecordingIngestor("dummy_v1"))

    registry_path = tmp_path / "agent_registry.yaml"
    registry_path.write_text(
        "\n".join(
            [
                "- agent_id: dummy_agent",
                "  agent_name: Dummy Agent",
                "  open_status: unknown",
                "  availability: audit_only",
                "  tier: extended",
                "  trajectory_format: dummy_v1",
                "",
            ]
        ),
        encoding="utf-8",
    )

    input_path = tmp_path / "traj.jsonl"
    input_path.write_text('{"hello":"world"}\n', encoding="utf-8")
    output_dir = tmp_path / "out"

    rc = main(
        [
            "--agent_id",
            "dummy_agent",
            "--trajectory",
            str(input_path),
            "--output",
            str(output_dir),
            "--registry",
            str(registry_path),
        ]
    )
    assert rc == 0

    called = json.loads((output_dir / "called.json").read_text(encoding="utf-8"))
    assert called["format_id"] == "dummy_v1"
    assert called["input_path"] == str(input_path)
    assert called["agent_id"] == "dummy_agent"
    assert isinstance(called["registry_entry"], dict)
    assert called["registry_entry"]["trajectory_format"] == "dummy_v1"
