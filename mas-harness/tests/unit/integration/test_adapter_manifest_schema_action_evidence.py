from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import jsonschema
import pytest

from mas_harness.integration.agents.registry import load_agent_registry


def _load_schema() -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[4]
    schema_path = repo_root / "mas-harness/src/mas_harness/schemas/adapter_manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert isinstance(schema, dict)
    return schema


def test_adapter_manifest_schema_loadable() -> None:
    schema = _load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)


def test_adapter_manifest_schema_requires_default_coord_space() -> None:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)

    ok = {"agent_id": "example", "default_coord_space": "physical_px"}
    validator.validate(ok)

    missing = {"agent_id": "example"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(missing)


def test_adapter_manifest_schema_action_evidence_optional_and_validated() -> None:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)

    base = {"agent_id": "example", "default_coord_space": "physical_px"}
    validator.validate(base)

    l1_ok = {
        **base,
        "action_evidence": {
            "level": "L1",
            "source": "agent_events",
            "event_stream": {"format": "agent_events_v1", "path_on_host": "agent_events.jsonl"},
        },
    }
    validator.validate(l1_ok)

    l1_bad_source = {
        **base,
        "action_evidence": {
            "level": "L1",
            "source": "comm_proxy",
            "event_stream": {"format": "agent_events_v1", "path_on_host": "agent_events.jsonl"},
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(l1_bad_source)

    l2_ok = {
        **base,
        "action_evidence": {
            "level": "L2",
            "source": "comm_proxy",
            "event_stream": {
                "format": "comm_proxy_trace_v1",
                "path_on_host": "comm_proxy_trace.jsonl",
            },
        },
    }
    validator.validate(l2_ok)

    l2_bad_source = {
        **base,
        "action_evidence": {
            "level": "L2",
            "source": "agent_events",
            "event_stream": {
                "format": "comm_proxy_trace_v1",
                "path_on_host": "comm_proxy_trace.jsonl",
            },
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(l2_bad_source)


def test_runnable_adapter_manifests_validate_against_schema() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)

    registry_path = repo_root / "mas-agents" / "registry" / "agent_registry.yaml"
    entries = load_agent_registry(registry_path)
    runnable = [e for e in entries if str(e.get("availability") or "").strip() == "runnable"]
    assert runnable, "expected at least one runnable registry entry for schema validation"

    for entry in runnable:
        agent_id = str(entry.get("agent_id") or "").strip()
        adapter_rel = entry.get("adapter")
        assert (
            isinstance(adapter_rel, str) and adapter_rel.strip()
        ), f"missing adapter path for {agent_id}"
        adapter_path = (repo_root / adapter_rel.strip()).resolve()
        manifest_path = adapter_path.parent / "adapter_manifest.json"
        assert (
            manifest_path.exists()
        ), f"missing adapter_manifest.json for runnable agent {agent_id}"

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(
            manifest, dict
        ), f"adapter_manifest.json must be an object: {manifest_path}"
        assert (
            str(manifest.get("agent_id") or "").strip() == agent_id
        ), f"adapter_manifest.agent_id mismatch for {agent_id}: {manifest_path}"
        validator.validate(manifest)
