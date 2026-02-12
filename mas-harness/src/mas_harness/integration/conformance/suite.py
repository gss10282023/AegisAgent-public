from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence

from mas_harness.cli import run_agent as run_agent_cli
from mas_harness.integration.agents.registry import discover_repo_root, load_agent_registry
from mas_harness.spec.validate_case import _default_schemas_dir, load_and_validate_case


@dataclass(frozen=True)
class ConformanceCase:
    case_dir: Path
    case_id: str


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _iter_case_dirs(cases_dir: Path) -> Sequence[Path]:
    """Return discovered case directories under `cases_dir`.

    A case directory is any directory containing a `task.yaml`.
    """
    if (cases_dir / "task.yaml").exists():
        return [cases_dir]

    return [p.parent for p in sorted(cases_dir.glob("**/task.yaml"))]


def discover_cases(*, cases_dir: Path, schemas_dir: Path) -> List[ConformanceCase]:
    cases: List[ConformanceCase] = []
    for case_dir in _iter_case_dirs(cases_dir):
        specs = load_and_validate_case(case_dir=case_dir, schemas_dir=schemas_dir)
        case_id = str(specs.task.get("task_id", case_dir.name))
        cases.append(ConformanceCase(case_dir=case_dir, case_id=case_id))
    return cases


def _cmd_list_cases(*, cases_dir: Path, schemas_dir: Path) -> int:
    cases = discover_cases(cases_dir=cases_dir, schemas_dir=schemas_dir)
    print(f"Discovered {len(cases)} case(s) under {cases_dir}")
    for c in cases:
        print(f"- {c.case_id}\t{c.case_dir}")
    return 0


def _parse_filter_expr(expr: str) -> list[tuple[str, set[str]]]:
    """Parse a simple comma-separated k=v filter expression.

    Supported syntax:
    - "key=value" (case-insensitive match)
    - Multiple allowed values: "key=v1|v2"
    """

    clauses: list[tuple[str, set[str]]] = []
    for raw_clause in str(expr or "").split(","):
        clause = raw_clause.strip()
        if not clause:
            continue
        if "=" not in clause:
            raise ValueError(f"invalid filter clause (expected k=v): {clause!r}")
        key, raw_values = clause.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid filter clause (empty key): {clause!r}")
        values = {v.strip().lower() for v in raw_values.split("|") if v.strip()}
        if not values:
            raise ValueError(f"invalid filter clause (empty value): {clause!r}")
        clauses.append((key, values))
    return clauses


def filter_registry_entries(
    entries: Sequence[dict[str, Any]],
    *,
    filter_expr: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return registry entries matching `filter_expr` (AND across clauses)."""

    if not filter_expr:
        return [e for e in entries if isinstance(e, dict)]

    clauses = _parse_filter_expr(filter_expr)
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ok = True
        for key, allowed in clauses:
            value = entry.get(key)
            if isinstance(value, list):
                matched = any(str(item).strip().lower() in allowed for item in value)
            else:
                matched = str(value or "").strip().lower() in allowed
            if not matched:
                ok = False
                break
        if ok:
            filtered.append(entry)
    return filtered


def _safe_dir_component(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return "unknown"
    out = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    normalized = "".join(out).strip("._")
    return normalized or "unknown"


def _select_execution_mode(entry: dict[str, Any]) -> str:
    modes = entry.get("execution_mode_supported")
    if isinstance(modes, list) and any(str(m).strip() == "agent_driven" for m in modes):
        return "agent_driven"
    return "planner_only"


def _should_enable_comm_proxy(entry: dict[str, Any]) -> bool:
    level = str(entry.get("action_trace_level") or "").strip().upper()
    return level == "L2"


def _cmd_run_suite(
    *,
    cases_dir: Path,
    schemas_dir: Path,
    registry_path: Path,
    output_dir: Path,
    filter_expr: Optional[str],
    seed: int,
) -> int:
    cases = discover_cases(cases_dir=cases_dir, schemas_dir=schemas_dir)
    if not cases:
        raise SystemExit(f"no conformance cases found under: {cases_dir}")

    entries = load_agent_registry(registry_path)
    selected = filter_registry_entries(entries, filter_expr=filter_expr)
    agent_ids: list[str] = []
    for entry in selected:
        agent_id = str(entry.get("agent_id") or "").strip()
        if agent_id:
            agent_ids.append(agent_id)

    if not agent_ids:
        raise SystemExit(f"no agents matched filter={filter_expr!r} in {registry_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    suite_meta = {
        "schemas_dir": str(schemas_dir),
        "cases_dir": str(cases_dir),
        "registry_path": str(registry_path),
        "filter": filter_expr,
        "cases": [c.case_id for c in cases],
        "agents": agent_ids,
        "seed": int(seed),
    }
    (output_dir / "suite_manifest.json").write_text(
        _json_dumps_canonical(suite_meta) + "\n",
        encoding="utf-8",
    )

    overall_rc = 0
    results: list[dict[str, Any]] = []
    for entry in selected:
        agent_id = str(entry.get("agent_id") or "").strip()
        if not agent_id:
            continue
        agent_out = output_dir / _safe_dir_component(agent_id)

        argv: list[str] = [
            "--agent_id",
            agent_id,
            "--case_dir",
            str(cases_dir),
            "--output",
            str(agent_out),
            "--registry",
            str(registry_path),
            "--seed",
            str(int(seed)),
            "--schemas_dir",
            str(schemas_dir),
            "--execution_mode",
            _select_execution_mode(entry),
        ]
        if _should_enable_comm_proxy(entry):
            argv += ["--comm_proxy_mode", "record"]

        rc = int(run_agent_cli.main(argv))
        results.append({"agent_id": agent_id, "rc": rc, "output_dir": str(agent_out)})
        if rc != 0:
            overall_rc = 2

    (output_dir / "suite_results.json").write_text(
        _json_dumps_canonical({"results": results}) + "\n",
        encoding="utf-8",
    )
    return overall_rc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="MAS conformance suite (discovery + spec validation)."
    )
    parser.add_argument(
        "--schemas_dir",
        type=Path,
        default=None,
        help="Directory containing MAS JSON schemas (default: repo_root/mas-spec/schemas)",
    )
    parser.add_argument(
        "--list_cases",
        type=Path,
        default=None,
        help="Dry-run: validate specs and list cases under this directory",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=None,
        help="Run conformance cases discovered under this directory",
    )
    parser.add_argument(
        "--agents_from_registry",
        type=Path,
        default=None,
        help="Load agent list from agent_registry.yaml for batch conformance runs",
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        help=(
            "Comma-separated filter for registry entries "
            "(example: availability=runnable,tier=core)"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Suite output directory (contains per-agent run dirs)",
    )
    parser.add_argument("--seed", type=int, default=0, help="Seed forwarded to per-agent runs")
    args = parser.parse_args(argv)

    schemas_dir = args.schemas_dir or _default_schemas_dir()
    if schemas_dir is None:
        raise SystemExit(
            "Unable to locate schemas dir. Pass --schemas_dir (expected: mas-spec/schemas)."
        )

    if args.list_cases is not None:
        return _cmd_list_cases(cases_dir=args.list_cases, schemas_dir=schemas_dir)

    if args.cases is not None:
        if args.agents_from_registry is None:
            parser.error("--agents_from_registry is required with --cases")
        if args.output is None:
            parser.error("--output is required with --cases")

        repo_root = discover_repo_root(args.agents_from_registry)
        if args.cases.is_absolute():
            cases_dir = args.cases
        else:
            cases_dir = (repo_root / args.cases).resolve()
        registry_path = (
            (repo_root / args.agents_from_registry).resolve()
            if not args.agents_from_registry.is_absolute()
            else args.agents_from_registry
        )
        if args.output.is_absolute():
            output_dir = args.output
        else:
            output_dir = (repo_root / args.output).resolve()
        schemas_dir = schemas_dir.resolve()

        return _cmd_run_suite(
            cases_dir=cases_dir,
            schemas_dir=schemas_dir,
            registry_path=registry_path,
            output_dir=output_dir,
            filter_expr=args.filter,
            seed=int(args.seed),
        )

    raise SystemExit("No command specified. Try --list_cases <dir> or --cases <dir> ...")


if __name__ == "__main__":
    raise SystemExit(main())
