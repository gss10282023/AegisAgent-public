from __future__ import annotations

from pathlib import Path

from mas_harness.evidence import _TINY_PNG_1X1, EvidenceWriter
from mas_harness.runtime.android.executor import AndroidExecutor


class _FakeController:
    def adb_shell(self, command: str, *, timeout_s: float, check: bool):  # pragma: no cover
        raise AssertionError(f"adb_shell should not be called in guard tests: {command}")


def _base_observation(*, package: str) -> dict:
    return {
        "screenshot_png": _TINY_PNG_1X1,
        "screen_info": {
            "width_px": 100,
            "height_px": 200,
            "density_dpi": 320,
            "surface_orientation": 0,
        },
        "foreground": {"package": package, "activity": "MainActivity"},
        "a11y_tree": {"nodes": [{"id": "root", "role": "window", "children": []}]},
    }


def test_obs_digest_changes_between_observations(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case_obs_digest", seed=0)
    writer.record_observation(step=0, observation=_base_observation(package="com.example.a"))
    d1 = writer.last_obs_digest
    writer.record_observation(step=1, observation=_base_observation(package="com.example.b"))
    d2 = writer.last_obs_digest
    writer.close()

    assert isinstance(d1, str) and d1
    assert isinstance(d2, str) and d2
    assert d1 != d2


def test_executor_rejects_stale_ref_obs_digest_and_marks_agent_failed(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case_ref_guard", seed=0)
    writer.record_observation(step=0, observation=_base_observation(package="com.example.a"))
    d1 = writer.last_obs_digest
    writer.record_observation(step=1, observation=_base_observation(package="com.example.b"))
    d2 = writer.last_obs_digest
    writer.close()

    assert isinstance(d1, str) and isinstance(d2, str)
    executor = AndroidExecutor(controller=_FakeController())
    executor.set_current_obs_digest(d2)

    res = executor.execute(
        {
            "type": "tap",
            "coord_space": "physical_px",
            "coord": {"x_px": 1, "y_px": 2, "x_norm": None, "y_norm": None},
            "ref_check_applicable": True,
            "ref_obs_digest": d1,
        }
    )
    assert res["ok"] is False
    assert res["error"] == "ref_obs_digest_mismatch"
    assert res.get("agent_failed") is True
