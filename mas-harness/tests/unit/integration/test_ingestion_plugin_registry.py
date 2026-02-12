from __future__ import annotations

from typing import Any

import pytest

from mas_harness.integration.ingestion import registry


class _DummyIngestor:
    def __init__(self, format_id: str, *, probe_substring: str) -> None:
        self.format_id = format_id
        self._probe_substring = probe_substring

    def probe(self, input_path: str) -> bool:
        return self._probe_substring in input_path

    def ingest(
        self,
        input_path: str,
        output_dir: str,
        *,
        agent_id: str | None = None,
        registry_entry: dict[str, Any] | None = None,
    ) -> None:
        return None


@pytest.fixture()
def _fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_REGISTRY", {})


def test_register_and_get_ingestor_by_format(_fresh_registry: None) -> None:
    ingestor = _DummyIngestor("dummy_v1", probe_substring="dummy_v1")
    registry.register_ingestor(ingestor)

    resolved = registry.get_ingestor_by_format("dummy_v1")
    assert resolved is ingestor


def test_get_ingestor_by_format_unknown(_fresh_registry: None) -> None:
    with pytest.raises(ValueError, match="unknown ingestor format_id"):
        registry.get_ingestor_by_format("missing_v1")


def test_register_ingestor_rejects_duplicate_format_id(_fresh_registry: None) -> None:
    registry.register_ingestor(_DummyIngestor("dup_v1", probe_substring="x"))
    with pytest.raises(ValueError, match="duplicate ingestor format_id"):
        registry.register_ingestor(_DummyIngestor("dup_v1", probe_substring="y"))


def test_auto_detect_returns_none_when_no_match(_fresh_registry: None) -> None:
    registry.register_ingestor(_DummyIngestor("a_v1", probe_substring="needle"))
    assert registry.auto_detect("nope.txt") is None


def test_auto_detect_returns_ingestor_when_single_match(_fresh_registry: None) -> None:
    a = _DummyIngestor("a_v1", probe_substring="AAA")
    b = _DummyIngestor("b_v1", probe_substring="BBB")
    registry.register_ingestor(a)
    registry.register_ingestor(b)

    assert registry.auto_detect("path/BBB.data") is b


def test_auto_detect_raises_on_ambiguous_match(_fresh_registry: None) -> None:
    registry.register_ingestor(_DummyIngestor("a_v1", probe_substring="X"))
    registry.register_ingestor(_DummyIngestor("b_v1", probe_substring="X"))
    with pytest.raises(ValueError, match="ambiguous trajectory format"):
        registry.auto_detect("file_with_X")
