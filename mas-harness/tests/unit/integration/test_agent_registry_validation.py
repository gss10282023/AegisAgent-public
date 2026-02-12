from __future__ import annotations

from mas_harness.integration.agents.registry import validate_agent_registry


def test_registry_requires_snapshot_coverage() -> None:
    snapshot = {
        "entries": [{"id": "a", "name": "A", "open_status": "open"}, {"id": "b", "name": "B"}]
    }
    registry_entries = [
        {
            "agent_id": "a",
            "agent_name": "A",
            "open_status": "open",
            "availability": "unavailable",
            "unavailable_reason": "x",
        }
    ]

    report = validate_agent_registry(snapshot, registry_entries)
    assert any(
        e.code == "registry.missing_snapshot_entry" and e.agent_id == "b" for e in report.errors
    )


def test_registry_detects_duplicate_agent_id() -> None:
    snapshot = {"entries": [{"id": "a", "name": "A", "open_status": "open"}]}
    registry_entries = [
        {
            "agent_id": "a",
            "agent_name": "A",
            "open_status": "open",
            "availability": "unavailable",
            "unavailable_reason": "x",
        },
        {
            "agent_id": "a",
            "agent_name": "A2",
            "open_status": "open",
            "availability": "unavailable",
            "unavailable_reason": "y",
        },
    ]
    report = validate_agent_registry(snapshot, registry_entries)
    assert any(e.code == "registry.duplicate_agent_id" and e.agent_id == "a" for e in report.errors)


def test_registry_requires_adapter_for_runnable() -> None:
    snapshot = {"entries": [{"id": "a", "name": "A", "open_status": "open"}]}
    registry_entries = [
        {
            "agent_id": "a",
            "agent_name": "A",
            "open_status": "open",
            "availability": "runnable",
        }
    ]
    report = validate_agent_registry(snapshot, registry_entries)
    assert any(e.code == "registry.missing_adapter" and e.agent_id == "a" for e in report.errors)


def test_registry_requires_ingest_or_trajectory_format_for_audit_only() -> None:
    snapshot = {"entries": [{"id": "a", "name": "A", "open_status": "open"}]}
    registry_entries = [
        {
            "agent_id": "a",
            "agent_name": "A",
            "open_status": "open",
            "availability": "audit_only",
        }
    ]
    report = validate_agent_registry(snapshot, registry_entries)
    assert any(
        e.code == "registry.missing_ingest_or_trajectory_format" and e.agent_id == "a"
        for e in report.errors
    )


def test_registry_requires_unavailable_reason_for_unavailable() -> None:
    snapshot = {"entries": [{"id": "a", "name": "A", "open_status": "open"}]}
    registry_entries = [
        {
            "agent_id": "a",
            "agent_name": "A",
            "open_status": "open",
            "availability": "unavailable",
        }
    ]
    report = validate_agent_registry(snapshot, registry_entries)
    assert any(
        e.code == "registry.missing_unavailable_reason" and e.agent_id == "a" for e in report.errors
    )


def test_registry_valid_example() -> None:
    snapshot = {
        "entries": [
            {"id": "a", "name": "A", "open_status": "open"},
            {"id": "b", "name": "B", "open_status": "closed"},
        ]
    }
    registry_entries = [
        {
            "agent_id": "a",
            "agent_name": "A",
            "open_status": "open",
            "availability": "unavailable",
            "unavailable_reason": "integration pending",
        },
        {
            "agent_id": "b",
            "agent_name": "B",
            "open_status": "closed",
            "availability": "runnable",
            "adapter": "mas-agents/adapters/b/adapter.py",
        },
    ]

    report = validate_agent_registry(snapshot, registry_entries, allow_extra_registry_entries=False)
    assert report.errors == []
