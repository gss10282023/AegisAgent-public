from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
from pathlib import Path
from typing import Optional

from mas_harness.integration.agentctl import core as agentctl_core
from mas_harness.integration.agents.registry import discover_repo_root
from mas_harness.runtime import runner

_REGISTRY_HELP = (
    "Optional override for agent_registry.yaml "
    "(default: repo mas-agents/registry/agent_registry.yaml)."
)


def _utc_timestamp() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y%m%d_%H%M%S")


def _print_agents(agents: list[agentctl_core.RunnableAgent]) -> None:
    if not agents:
        print("(no runnable agents with adapters found)")
        return

    cols = ["agent_id", "availability", "execution_mode", "action_trace_level", "env_profile"]
    width = {c: max(len(c), *(len(str(getattr(a, c))) for a in agents)) for c in cols}
    header = "  ".join(c.ljust(width[c]) for c in cols)
    print(header)
    print("-" * len(header))
    for a in agents:
        row = "  ".join(str(getattr(a, c)).ljust(width[c]) for c in cols)
        print(row)


def _shell(
    *,
    repo_root: Path,
    registry_path: Path,
    schemas_dir: Path,
    device_serial: Optional[str],
    adb_path: str,
    execution_mode: str,
    env_profile: Optional[str],
    output_base: Optional[Path],
) -> int:
    current_agent: Optional[str] = None
    max_steps: int = 40

    def resolve_output(agent_id: str) -> Path:
        if output_base is None:
            return agentctl_core.default_agentctl_output_dir(repo_root=repo_root, agent_id=agent_id)
        return (output_base / agent_id / _utc_timestamp()).resolve()

    print("agentctl shell (type 'help' for commands)")
    while True:
        try:
            prompt_agent = current_agent or "<none>"
            line = input(f"agentctl[{prompt_agent}]> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue

        if not line:
            continue
        parts = shlex.split(line)
        cmd = parts[0]

        if cmd in {"quit", "exit"}:
            return 0
        if cmd == "help":
            print(
                "\n".join(
                    [
                        "Commands:",
                        "  list",
                        "  use <agent_id>",
                        "  fixed",
                        "  nl <goal...>",
                        "  set max_steps <n>",
                        "  quit",
                    ]
                )
            )
            continue
        if cmd == "list":
            agents = agentctl_core.list_runnable_agents(
                registry_path=registry_path,
                repo_root=repo_root,
                include_missing_adapter=False,
            )
            _print_agents(agents)
            continue
        if cmd == "use":
            if len(parts) != 2:
                print("usage: use <agent_id>")
                continue
            current_agent = parts[1].strip()
            continue
        if cmd == "set":
            if len(parts) != 3 or parts[1] != "max_steps":
                print("usage: set max_steps <n>")
                continue
            try:
                max_steps = int(parts[2])
            except ValueError:
                print("max_steps must be an integer")
                continue
            continue
        if cmd in {"fixed", "nl"}:
            if current_agent is None:
                print("no agent selected (use <agent_id>)")
                continue

            if cmd == "nl":
                goal = " ".join(parts[1:]).strip()
                if not goal:
                    print("usage: nl <goal...>")
                    continue
                run_goal: Optional[str] = goal
            else:
                run_goal = None

            out_dir = resolve_output(current_agent)
            try:
                agentctl_core.run_agentctl_case(
                    agent_id=current_agent,
                    mode=cmd,
                    goal=run_goal,
                    max_steps=max_steps,
                    output_dir=out_dir,
                    registry_path=registry_path,
                    schemas_dir=schemas_dir,
                    execution_mode=execution_mode,
                    env_profile_override=env_profile,
                    repo_root=repo_root,
                    device_serial=device_serial,
                    adb_path=adb_path,
                    snapshot_tag=None,
                    reset_strategy=None,
                    seed=0,
                )
            except Exception as e:
                print(f"[ERROR] {e}")
                continue

            print(f"[OK] wrote run -> {out_dir}")
            continue

        print(f"unknown command: {cmd} (type 'help')")


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent integration verification CLI (agentctl).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_p = sub.add_parser("list", help="List runnable agents with adapters present.")
    list_p.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=_REGISTRY_HELP,
    )
    list_p.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    list_p.add_argument(
        "--include_missing_adapter",
        action="store_true",
        help="Include runnable entries whose adapter file is missing.",
    )

    fixed_p = sub.add_parser("fixed", help="Run a fixed smoke task to validate agent integration.")
    fixed_p.add_argument("--agent_id", type=str, required=True)
    fixed_p.add_argument("--device_serial", type=str, default=os.environ.get("MAS_ANDROID_SERIAL"))
    fixed_p.add_argument("--adb_path", type=str, default=os.environ.get("MAS_ADB_PATH", "adb"))
    fixed_p.add_argument("--output", type=Path, default=None)
    fixed_p.add_argument("--seed", type=int, default=0)
    fixed_p.add_argument(
        "--schemas_dir",
        type=Path,
        default=Path("mas-spec/schemas"),
        help="Directory containing JSON schemas (default: mas-spec/schemas).",
    )
    fixed_p.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=_REGISTRY_HELP,
    )
    fixed_p.add_argument(
        "--execution_mode",
        type=str,
        default=os.environ.get("MAS_EXECUTION_MODE", "planner_only"),
        choices=["planner_only", "agent_driven"],
    )
    fixed_p.add_argument(
        "--env_profile",
        type=str,
        default=os.environ.get("MAS_ENV_PROFILE"),
        choices=["mas_core", "android_world_compat"],
    )
    fixed_p.add_argument("--snapshot_tag", type=str, default=os.environ.get("MAS_SNAPSHOT_TAG"))
    fixed_p.add_argument(
        "--reset_strategy",
        type=str,
        default=os.environ.get("MAS_RESET_STRATEGY"),
        choices=["snapshot", "reinstall", "none"],
    )

    nl_p = sub.add_parser("nl", help="Run an ad-hoc natural language goal (debugging).")
    nl_p.add_argument("--agent_id", type=str, required=True)
    nl_p.add_argument("--device_serial", type=str, default=os.environ.get("MAS_ANDROID_SERIAL"))
    nl_p.add_argument("--adb_path", type=str, default=os.environ.get("MAS_ADB_PATH", "adb"))
    nl_p.add_argument("--goal", type=str, required=True)
    nl_p.add_argument("--max_steps", type=int, default=40)
    nl_p.add_argument("--output", type=Path, default=None)
    nl_p.add_argument("--seed", type=int, default=0)
    nl_p.add_argument(
        "--schemas_dir",
        type=Path,
        default=Path("mas-spec/schemas"),
        help="Directory containing JSON schemas (default: mas-spec/schemas).",
    )
    nl_p.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=_REGISTRY_HELP,
    )
    nl_p.add_argument(
        "--execution_mode",
        type=str,
        default=os.environ.get("MAS_EXECUTION_MODE", "planner_only"),
        choices=["planner_only", "agent_driven"],
    )
    nl_p.add_argument(
        "--env_profile",
        type=str,
        default=os.environ.get("MAS_ENV_PROFILE"),
        choices=["mas_core", "android_world_compat"],
    )
    nl_p.add_argument("--snapshot_tag", type=str, default=os.environ.get("MAS_SNAPSHOT_TAG"))
    nl_p.add_argument(
        "--reset_strategy",
        type=str,
        default=os.environ.get("MAS_RESET_STRATEGY"),
        choices=["snapshot", "reinstall", "none"],
    )

    shell_p = sub.add_parser("shell", help="Interactive agentctl shell.")
    shell_p.add_argument("--device_serial", type=str, default=os.environ.get("MAS_ANDROID_SERIAL"))
    shell_p.add_argument("--adb_path", type=str, default=os.environ.get("MAS_ADB_PATH", "adb"))
    shell_p.add_argument(
        "--schemas_dir",
        type=Path,
        default=Path("mas-spec/schemas"),
        help="Directory containing JSON schemas (default: mas-spec/schemas).",
    )
    shell_p.add_argument(
        "--registry",
        type=Path,
        default=None,
        help=_REGISTRY_HELP,
    )
    shell_p.add_argument(
        "--execution_mode",
        type=str,
        default=os.environ.get("MAS_EXECUTION_MODE", "planner_only"),
        choices=["planner_only", "agent_driven"],
    )
    shell_p.add_argument(
        "--env_profile",
        type=str,
        default=os.environ.get("MAS_ENV_PROFILE"),
        choices=["mas_core", "android_world_compat"],
    )
    shell_p.add_argument(
        "--output_base",
        type=Path,
        default=None,
        help="Base directory for shell runs (default: runs/agentctl/<agent_id>/<timestamp>).",
    )

    args = parser.parse_args()

    repo_root = discover_repo_root()
    default_registry_path = repo_root / "mas-agents" / "registry" / "agent_registry.yaml"

    if args.cmd == "list":
        registry_path = args.registry or default_registry_path
        agents = agentctl_core.list_runnable_agents(
            registry_path=registry_path,
            repo_root=repo_root,
            include_missing_adapter=bool(args.include_missing_adapter),
        )
        if args.json:
            print(json.dumps([a.as_dict() for a in agents], indent=2, ensure_ascii=False))
        else:
            _print_agents(agents)
        return 0

    if args.cmd in {"fixed", "nl"}:
        registry_path = args.registry or default_registry_path
        output_dir = (
            runner._resolve_path(repo_root, str(args.output))
            if args.output is not None
            else agentctl_core.default_agentctl_output_dir(
                repo_root=repo_root, agent_id=args.agent_id
            )
        )

        agentctl_core.run_agentctl_case(
            agent_id=str(args.agent_id).strip(),
            mode=args.cmd,
            goal=(str(args.goal) if args.cmd == "nl" else None),
            max_steps=int(getattr(args, "max_steps", 40)),
            output_dir=output_dir,
            registry_path=registry_path,
            schemas_dir=args.schemas_dir,
            execution_mode=args.execution_mode,
            env_profile_override=args.env_profile,
            repo_root=repo_root,
            device_serial=args.device_serial,
            adb_path=args.adb_path,
            snapshot_tag=getattr(args, "snapshot_tag", None),
            reset_strategy=getattr(args, "reset_strategy", None),
            seed=int(args.seed),
        )
        print(f"Wrote run -> {output_dir}")
        return 0

    if args.cmd == "shell":
        registry_path = args.registry or default_registry_path
        output_base = (
            runner._resolve_path(repo_root, str(args.output_base))
            if args.output_base is not None
            else None
        )
        return _shell(
            repo_root=repo_root,
            registry_path=registry_path,
            schemas_dir=args.schemas_dir,
            device_serial=args.device_serial,
            adb_path=args.adb_path,
            execution_mode=args.execution_mode,
            env_profile=args.env_profile,
            output_base=output_base,
        )

    raise SystemExit(f"unknown subcommand: {args.cmd}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
