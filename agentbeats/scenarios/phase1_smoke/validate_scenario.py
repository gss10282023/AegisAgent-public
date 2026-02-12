from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn


def _fail(msg: str) -> NoReturn:
    print(f"[phase1_smoke] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    scenario_path = Path("agentbeats/scenarios/phase1_smoke/scenario.toml")
    if not scenario_path.exists():
        _fail(f"missing scenario: {scenario_path}")

    try:
        import tomllib  # py311+
    except Exception:  # pragma: no cover
        _fail("Python 3.11+ required (tomllib not available)")

    data = tomllib.loads(scenario_path.read_text(encoding="utf-8"))

    green = data.get("green_agent")
    if not isinstance(green, dict):
        _fail("missing [green_agent]")
    if not green.get("image"):
        _fail("missing [green_agent].image")

    participants = data.get("participants")
    if not isinstance(participants, list) or not participants:
        _fail("missing [[participants]]")
    if not isinstance(participants[0], dict) or not participants[0].get("image"):
        _fail("missing [[participants]].image")

    config = data.get("config")
    if not isinstance(config, dict):
        _fail("missing [config]")

    print("[phase1_smoke] OK: agentbeats scaffold + scenario.toml present")
    print(f"[phase1_smoke] green_agent.image = {green['image']}")
    print(f"[phase1_smoke] participants[0].image = {participants[0]['image']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
