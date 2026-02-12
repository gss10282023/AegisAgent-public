from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from mas_harness.integration.agents.registry import discover_repo_root, load_agent_registry
from mas_harness.integration.ingestion.registry import get_ingestor_by_format


def _find_registry_entry(
    entries: Iterable[Dict[str, Any]],
    agent_id: str,
) -> Optional[Dict[str, Any]]:
    needle = agent_id.strip()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("agent_id") or "").strip() == needle:
            return entry
    return None


def _resolve_format_from_registry(*, agent_id: str, registry_path: Path) -> dict[str, Any]:
    entries = load_agent_registry(registry_path)
    registry_entry = _find_registry_entry(entries, agent_id)
    if registry_entry is None:
        raise ValueError(f"agent_id not found in registry: {agent_id} ({registry_path})")

    availability = str(registry_entry.get("availability") or "").strip()
    if availability != "audit_only":
        raise ValueError(
            f"registry availability for {agent_id} must be audit_only (got {availability!r})"
        )

    format_id = str(registry_entry.get("trajectory_format") or "").strip()
    if not format_id:
        raise ValueError(f"registry trajectory_format missing for agent_id={agent_id}")

    return {
        "format_id": format_id,
        "registry_entry": registry_entry,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest an agent-exported trajectory/log into a MAS evidence bundle "
            "(Phase3 audit_only ingestion)."
        )
    )
    parser.add_argument(
        "--format",
        type=str,
        default=None,
        help=(
            "Explicit trajectory format_id to ingest (e.g. androidworld_jsonl, "
            "droidrun_events_v1)."
        ),
    )
    parser.add_argument(
        "--agent_id",
        type=str,
        default=None,
        help=(
            "Agent id used to auto-resolve trajectory_format from agent_registry.yaml "
            "when --format is not provided."
        ),
    )
    parser.add_argument(
        "--input",
        "--trajectory",
        dest="input_path",
        type=Path,
        required=True,
        help="Path to the input trajectory/log file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory (evidence bundle root).",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=(
            "Optional override for agent_registry.yaml "
            "(default: repo mas-agents/registry/agent_registry.yaml)."
        ),
    )

    args = parser.parse_args(argv)

    raw_agent_id = str(args.agent_id).strip() if args.agent_id is not None else ""
    agent_id = raw_agent_id or None
    if args.agent_id is not None and agent_id is None:
        parser.error("--agent_id must be non-empty")

    raw_format_id = str(args.format).strip() if args.format is not None else ""
    format_id = raw_format_id or None
    if args.format is not None and format_id is None:
        parser.error("--format must be non-empty")

    input_path: Path = args.input_path
    if not input_path.exists():
        parser.error(f"--input not found: {input_path}")

    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)

    registry_entry: dict[str, Any] | None = None
    if format_id is None:
        if agent_id is None:
            parser.error("either --format or --agent_id must be provided")

        repo_root = discover_repo_root()
        registry_path = args.registry or (
            repo_root / "mas-agents" / "registry" / "agent_registry.yaml"
        )

        try:
            resolved = _resolve_format_from_registry(agent_id=agent_id, registry_path=registry_path)
        except Exception as e:
            print(f"[ERROR] {e}")
            return 2

        format_id = str(resolved["format_id"])
        registry_entry = resolved["registry_entry"]

    try:
        ingestor = get_ingestor_by_format(format_id)
    except Exception as e:
        print(f"[ERROR] {e}")
        return 2

    try:
        ingestor.ingest(
            str(input_path),
            str(output),
            agent_id=agent_id,
            registry_entry=registry_entry,
        )
    except Exception as e:
        print(f"[ERROR] ingest failed: {e}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
