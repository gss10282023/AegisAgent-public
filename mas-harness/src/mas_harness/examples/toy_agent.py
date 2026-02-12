from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ToyAgent:
    """A minimal agent for phase-0 smoke tests.

    It emits `wait` actions for a fixed number of steps, then emits `finished`.
    """

    stop_after: int = 2
    _steps_emitted: int = 0
    _open_package: str | None = None

    def reset(
        self,
        user_goal: str,
        policy: Dict[str, Any],
        *,
        stop_after: int | None = None,
    ) -> None:
        goal = str(user_goal or "")
        del policy
        self._steps_emitted = 0
        goal_lower = goal.lower()
        if "com.android.settings" in goal_lower or "settings" in goal_lower or "设置" in goal:
            self._open_package = "com.android.settings"
        else:
            self._open_package = None
        if stop_after is not None:
            self.stop_after = stop_after

    def step(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        del observation  # phase-0 ignores observations
        if self._steps_emitted == 0 and self._open_package is not None:
            self._steps_emitted += 1
            return {
                "type": "open_app",
                "package": self._open_package,
                "note": "toy_agent fixed open_app",
            }
        if self._steps_emitted >= self.stop_after:
            return {"type": "finished"}
        self._steps_emitted += 1
        return {"type": "wait", "duration_ms": 500, "note": "phase-0 smoke action"}
