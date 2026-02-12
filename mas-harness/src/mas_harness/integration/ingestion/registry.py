"""Trajectory ingestion plugin registry (Phase 3c 3c-1a).

This module defines the minimal plugin interface used by audit_only ingestion.
Ingestors are registered by `format_id` and can be retrieved directly or
auto-detected by probing an input path.
"""

from __future__ import annotations

from typing import Any, Protocol


class TrajectoryIngestor(Protocol):
    format_id: str

    def probe(self, input_path: str) -> bool: ...

    def ingest(
        self,
        input_path: str,
        output_dir: str,
        *,
        agent_id: str | None = None,
        registry_entry: dict[str, Any] | None = None,
    ) -> None: ...


_REGISTRY: dict[str, TrajectoryIngestor] = {}


def register_ingestor(ingestor: TrajectoryIngestor) -> None:
    """Register an ingestor implementation by its `format_id`."""

    raw_format_id = getattr(ingestor, "format_id", None)
    if not isinstance(raw_format_id, str):
        raise ValueError("ingestor.format_id must be a string")

    format_id = raw_format_id.strip()
    if not format_id:
        raise ValueError("ingestor.format_id must be a non-empty string")
    if format_id != raw_format_id:
        raise ValueError("ingestor.format_id must not contain leading/trailing whitespace")

    if format_id in _REGISTRY:
        raise ValueError(f"duplicate ingestor format_id: {format_id}")
    _REGISTRY[format_id] = ingestor


def get_ingestor_by_format(format_id: str) -> TrajectoryIngestor:
    """Return the ingestor registered for the given `format_id`."""

    if not isinstance(format_id, str):
        raise ValueError("format_id must be a string")
    format_id = format_id.strip()
    if not format_id:
        raise ValueError("format_id must be a non-empty string")

    ingestor = _REGISTRY.get(format_id)
    if ingestor is None:
        raise ValueError(f"unknown ingestor format_id: {format_id}")
    return ingestor


def auto_detect(input_path: str) -> TrajectoryIngestor | None:
    """Return the single ingestor that claims `input_path`, or None if no match.

    Raises ValueError if multiple ingestors match.
    """

    matches: list[TrajectoryIngestor] = []
    for _, ingestor in sorted(_REGISTRY.items()):
        if ingestor.probe(input_path):
            matches.append(ingestor)

    if not matches:
        return None
    if len(matches) > 1:
        format_ids = sorted(i.format_id for i in matches if isinstance(i.format_id, str))
        raise ValueError(f"ambiguous trajectory format for {input_path!r}: {format_ids}")
    return matches[0]


def available_ingestors() -> dict[str, TrajectoryIngestor]:
    """Return a snapshot of the current registry."""

    return dict(_REGISTRY)
