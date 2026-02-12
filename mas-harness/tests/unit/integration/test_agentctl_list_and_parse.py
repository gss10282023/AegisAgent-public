from __future__ import annotations

import json
import sys
from pathlib import Path


def _run_cli(argv: list[str]) -> int:
    from mas_harness.cli import agentctl

    old_argv = sys.argv
    try:
        sys.argv = argv
        return int(agentctl.main())
    finally:
        sys.argv = old_argv


def test_agentctl_list_json_parses_and_includes_toy_agent(capsys) -> None:
    rc = _run_cli(["agentctl", "list", "--json"])
    assert rc == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)

    toy = next((item for item in data if item.get("agent_id") == "toy_agent"), None)
    assert toy is not None
    assert toy["availability"] == "runnable"
    assert toy["execution_mode"] == "planner_only"
    assert toy["action_trace_level"] == "L0"
    assert toy["env_profile"] == "android_world_compat"

    adapter_path = Path(toy["adapter_path"])
    assert adapter_path.name == "adapter.py"
    assert toy["adapter_exists"] is True
