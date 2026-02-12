from __future__ import annotations

import argparse
from pathlib import Path

from mas_harness.spec.spec_loader import (
    SpecValidationError,
    discover_case,
    load_schema,
    load_yaml_or_json,
    validate_against_schema,
)


def iter_case_dirs(cases_dir: Path):
    """Yield case directories.

    A case directory is any directory containing a `task.yaml`.
    """
    if (cases_dir / "task.yaml").exists():
        yield cases_dir
        return

    for p in sorted(cases_dir.glob("**/task.yaml")):
        yield p.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MAS spec JSON schemas and case specs.")
    parser.add_argument(
        "--spec_dir",
        type=Path,
        required=True,
        help="Directory containing JSON schema files",
    )
    parser.add_argument(
        "--cases_dir",
        type=Path,
        required=True,
        help="Directory containing MAS cases",
    )
    args = parser.parse_args()

    task_schema = load_schema(args.spec_dir / "task_schema.json")
    policy_schema = load_schema(args.spec_dir / "policy_schema.json")
    eval_schema = load_schema(args.spec_dir / "eval_schema.json")
    attack_schema = load_schema(args.spec_dir / "attack_schema.json")

    count = 0
    for case_dir in iter_case_dirs(args.cases_dir):
        paths = discover_case(case_dir)
        try:
            task = load_yaml_or_json(paths.task)
            policy = load_yaml_or_json(paths.policy)
            ev = load_yaml_or_json(paths.eval)

            validate_against_schema(task, task_schema, where=str(paths.task))
            validate_against_schema(policy, policy_schema, where=str(paths.policy))
            validate_against_schema(ev, eval_schema, where=str(paths.eval))

            if paths.attack is not None:
                attack = load_yaml_or_json(paths.attack)
                validate_against_schema(attack, attack_schema, where=str(paths.attack))

            count += 1
        except (FileNotFoundError, SpecValidationError) as e:
            raise SystemExit(f"Spec validation failed for {case_dir}:\n{e}")

    print(f"Validated {count} case(s) under {args.cases_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
