from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from mas_harness.evidence import _TINY_PNG_1X1, stable_sha256


@dataclass
class ToyEnv:
    """A minimal environment for phase-0 smoke tests.

    It mimics the observation/action loop without Android.
    """

    step: int = 0

    def reset(self, initial_state: Dict[str, Any] | None = None) -> None:
        self.step = 0

    def observe(self) -> Dict[str, Any]:
        screen_id = "toy_screen"
        ui_text = f"toy step {self.step}"
        ui_hash = stable_sha256(f"{screen_id}|{ui_text}".encode("utf-8"))

        # The toy env produces *placeholders* that let us validate the evidence
        # pipeline (obs dumps + traces) without requiring an Android emulator.
        return {
            "foreground_package": "com.toy.app",
            "foreground_activity": "com.toy.app.ToyActivity",
            "screen_info": {
                "width_px": 1080,
                "height_px": 1920,
                "density_dpi": 440,
                "surface_orientation": 0,
            },
            # Always a valid (tiny) PNG so downstream tooling can treat it like a screenshot.
            "screenshot_png": _TINY_PNG_1X1,
            # Minimal a11y-like structure.
            "a11y_tree": {
                "nodes": [
                    {"id": "root", "role": "window", "children": ["label"]},
                    {
                        "id": "label",
                        "role": "text",
                        "text": ui_text,
                        "bounds": [0, 0, 200, 50],
                    },
                ]
            },
            "ui": {
                "screen_id": screen_id,
                "text": ui_text,
            },
            "ui_hash": ui_hash,
            "notifications": [],
            "clipboard": None,
        }

    def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        # In a real env, this would execute UI/tool actions. Here we just advance.
        self.step += 1
        return {
            "ok": True,
            "env_step": self.step,
            "executed": action,
        }
