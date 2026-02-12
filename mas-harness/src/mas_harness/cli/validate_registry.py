from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from mas_harness.integration.agents.registry import (
    default_env_profiles_dir,
    discover_repo_root,
    load_agent_registry,
    load_env_profile,
    validate_agent_registry_files,
    validate_core_agents,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate mas-agents/registry/agent_registry.yaml."
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Path to leaderboard_snapshot.json (required unless --print_env_profile).",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="Path to agent_registry.yaml (required unless --print_env_profile).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat snapshot metadata mismatches and extra registry entries as errors.",
    )
    parser.add_argument(
        "--core_only",
        action="store_true",
        help="Validate tier=core constraints only (does not require --snapshot).",
    )
    parser.add_argument(
        "--print_env_profile",
        type=str,
        default=None,
        help="Print an env profile YAML by id (e.g., mas_core).",
    )
    parser.add_argument(
        "--env_profiles_dir",
        type=Path,
        default=None,
        help="Optional override for env profiles directory.",
    )
    args = parser.parse_args()

    if args.print_env_profile:
        env_profiles_dir = args.env_profiles_dir or default_env_profiles_dir()
        profile = load_env_profile(env_profiles_dir, args.print_env_profile)
        print(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).rstrip())
        return 0

    if args.core_only:
        if args.registry is None:
            parser.error("--registry is required when --core_only is set")
        entries = load_agent_registry(args.registry)
        repo_root = discover_repo_root(args.registry)
        report = validate_core_agents(entries, repo_root=repo_root)
    else:
        if args.snapshot is None or args.registry is None:
            parser.error("--snapshot and --registry are required unless --print_env_profile is set")

        report = validate_agent_registry_files(
            snapshot_path=args.snapshot,
            registry_path=args.registry,
            allow_extra_registry_entries=not args.strict,
            enforce_snapshot_metadata_match=args.strict,
        )

    for issue in report.warnings:
        print(f"WARNING: {issue.format()}")
    if report.errors:
        for issue in report.errors:
            print(f"ERROR: {issue.format()}")
        return 1

    if args.core_only:
        print("OK: core agents validation passed")
    else:
        print("OK: registry validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
