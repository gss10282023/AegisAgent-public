from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from mas_harness.oracle_framework.policy_compile import compile_baseline_safety_assertions
from mas_harness.spec.spec_loader import (
    CaseSpecPaths,
    SpecValidationError,
    discover_case,
    load_schema,
    load_yaml_or_json,
    validate_against_schema,
)


@dataclass(frozen=True)
class LoadedCaseSpecs:
    paths: CaseSpecPaths
    task: Dict[str, Any]
    policy: Dict[str, Any]
    eval: Dict[str, Any]
    attack: Optional[Dict[str, Any]] = None


def _default_schemas_dir() -> Optional[Path]:
    from mas_harness.integration.agents.registry import discover_repo_root

    schemas_dir = discover_repo_root() / "mas-spec" / "schemas"
    return schemas_dir if schemas_dir.is_dir() else None


def load_and_validate_case(*, case_dir: Path, schemas_dir: Path) -> LoadedCaseSpecs:
    paths = discover_case(case_dir)

    task_schema = load_schema(schemas_dir / "task_schema.json")
    policy_schema = load_schema(schemas_dir / "policy_schema.json")
    eval_schema = load_schema(schemas_dir / "eval_schema.json")
    attack_schema = load_schema(schemas_dir / "attack_schema.json")

    task = load_yaml_or_json(paths.task)
    policy = load_yaml_or_json(paths.policy)
    ev = load_yaml_or_json(paths.eval)

    validate_against_schema(task, task_schema, where=str(paths.task))
    validate_against_schema(policy, policy_schema, where=str(paths.policy))
    validate_against_schema(ev, eval_schema, where=str(paths.eval))
    compiled = compile_baseline_safety_assertions(policy, eval_spec=ev)
    if not compiled:
        raise SpecValidationError(f"Baseline safety assertions compiled empty: {paths.policy}")

    attack: Optional[Dict[str, Any]] = None
    if paths.attack is not None:
        attack = load_yaml_or_json(paths.attack)
        validate_against_schema(attack, attack_schema, where=str(paths.attack))

    return LoadedCaseSpecs(paths=paths, task=task, policy=policy, eval=ev, attack=attack)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a single MAS case directory.")
    parser.add_argument(
        "--case_dir",
        type=Path,
        required=True,
        help="Case directory containing task.yaml/policy.yaml/eval.yaml",
    )
    parser.add_argument(
        "--schemas_dir",
        type=Path,
        default=None,
        help="Directory containing MAS JSON schemas (default: repo_root/mas-spec/schemas)",
    )
    args = parser.parse_args()

    schemas_dir = args.schemas_dir or _default_schemas_dir()
    if schemas_dir is None:
        raise SystemExit(
            "Unable to locate schemas dir. Pass --schemas_dir (expected: mas-spec/schemas)."
        )

    try:
        specs = load_and_validate_case(case_dir=args.case_dir, schemas_dir=schemas_dir)
    except (FileNotFoundError, SpecValidationError, ValueError) as e:
        raise SystemExit(f"Case validation failed for {args.case_dir}:\n{e}")

    case_id = str(specs.task.get("task_id", args.case_dir.name))
    print(f"OK: {case_id} ({args.case_dir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
