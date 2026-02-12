from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from jsonschema import Draft202012Validator


class SpecValidationError(RuntimeError):
    pass


def load_yaml_or_json(path: Path) -> Dict[str, Any]:
    """Load a YAML/JSON file into a dict.

    This is intentionally strict: the top-level must be an object.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"Unsupported spec file extension: {path}")

    if not isinstance(data, dict):
        raise SpecValidationError(f"Top-level spec must be an object: {path}")
    return data


def load_schema(schema_path: Path) -> Dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise SpecValidationError(f"Schema must be an object: {schema_path}")
    return schema


def validate_against_schema(
    instance: Dict[str, Any],
    schema: Dict[str, Any],
    *,
    where: str,
) -> None:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        msgs = []
        for e in errors[:20]:
            loc = "/".join([str(p) for p in e.path])
            msgs.append(f"- {where}:{loc}: {e.message}")
        if len(errors) > 20:
            msgs.append(f"... ({len(errors)-20} more)")
        raise SpecValidationError("\n".join(msgs))


@dataclass
class CaseSpecPaths:
    case_dir: Path
    task: Path
    policy: Path
    eval: Path
    attack: Optional[Path] = None


def discover_case(case_dir: Path) -> CaseSpecPaths:
    """Discover the canonical spec files in a case directory."""
    task = case_dir / "task.yaml"
    policy = case_dir / "policy.yaml"
    eval_path = case_dir / "eval.yaml"
    attack = case_dir / "attack.yaml"
    return CaseSpecPaths(
        case_dir=case_dir,
        task=task,
        policy=policy,
        eval=eval_path,
        attack=attack if attack.exists() else None,
    )
