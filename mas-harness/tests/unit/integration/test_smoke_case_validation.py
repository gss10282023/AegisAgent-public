from pathlib import Path

from mas_harness.spec.spec_loader import load_schema, load_yaml_or_json, validate_against_schema


def test_smoke_case_specs_validate() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas = repo_root / "mas-spec" / "schemas"
    case_dir = repo_root / "mas-public" / "cases" / "smoke_001"

    task_schema = load_schema(schemas / "task_schema.json")
    policy_schema = load_schema(schemas / "policy_schema.json")
    eval_schema = load_schema(schemas / "eval_schema.json")

    task = load_yaml_or_json(case_dir / "task.yaml")
    policy = load_yaml_or_json(case_dir / "policy.yaml")
    ev = load_yaml_or_json(case_dir / "eval.yaml")

    validate_against_schema(task, task_schema, where="task.yaml")
    validate_against_schema(policy, policy_schema, where="policy.yaml")
    validate_against_schema(ev, eval_schema, where="eval.yaml")
