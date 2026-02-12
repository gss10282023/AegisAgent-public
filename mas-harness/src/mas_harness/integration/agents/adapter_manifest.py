from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from mas_harness.integration.agents.base import AgentAdapterError


class AdapterManifestValidationError(AgentAdapterError):
    pass


def _schema_path() -> Path:
    # mas_harness/integration/agents/* → mas_harness/schemas/adapter_manifest.schema.json
    return Path(__file__).resolve().parents[2] / "schemas" / "adapter_manifest.schema.json"


@lru_cache(maxsize=1)
def _load_schema() -> dict[str, Any]:
    schema_path = _schema_path()
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AdapterManifestValidationError(f"schema must be an object: {schema_path}")
    jsonschema.Draft202012Validator.check_schema(data)
    return data


def validate_adapter_manifest(manifest: Mapping[str, Any], *, manifest_path: Path) -> None:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda err: list(err.path))
    if not errors:
        return

    msgs: list[str] = []
    for err in errors[:20]:
        loc = "/".join(str(p) for p in err.path)
        suffix = f":{loc}" if loc else ""
        msgs.append(f"- {manifest_path}{suffix}: {err.message}")
    if len(errors) > 20:
        msgs.append(f"... ({len(errors)-20} more)")

    raise AdapterManifestValidationError(
        "adapter_manifest.json schema validation failed:\n" + "\n".join(msgs)
    )


def load_adapter_manifest(manifest_path: Path, *, validate: bool = True) -> dict[str, Any]:
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    try:
        obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise AdapterManifestValidationError(
            f"invalid adapter_manifest.json: {manifest_path} ({e})"
        ) from e
    if not isinstance(obj, dict):
        raise AdapterManifestValidationError(
            f"adapter_manifest.json must be an object: {manifest_path}"
        )
    if validate:
        validate_adapter_manifest(obj, manifest_path=manifest_path)
    return obj
