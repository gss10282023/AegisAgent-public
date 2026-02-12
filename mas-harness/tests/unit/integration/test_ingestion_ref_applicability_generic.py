from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mas_harness.evidence import _TINY_PNG_1X1, EvidenceWriter
from mas_harness.runtime import ref_obs_digest_consistency_decision


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def test_ingestion_downgrades_ref_checks_when_observation_missing(tmp_path: Path) -> None:
    writer = EvidenceWriter(
        run_dir=tmp_path, case_id="case_ingest_missing_obs", seed=0, run_mode="phase3"
    )

    # Missing observation (no screenshot + no geometry) should downgrade ref checks.
    writer.record_observation(step=0, observation={})
    normalized = writer.record_agent_action(step=0, action={"type": "tap", "x": 1, "y": 2})
    writer.close()

    obs_events = _read_jsonl(writer.paths.obs_trace)
    obs = next(e for e in obs_events if e.get("event") == "observation" and e.get("step") == 0)
    assert obs.get("obs_digest") is None

    assert normalized.get("auditability_limited") is True
    assert set(normalized.get("auditability_limits") or []) >= {"no_screenshot", "no_geometry"}

    assert "obs_digest" in normalized and normalized.get("obs_digest") is None
    assert normalized.get("ref_check_applicable") is False
    assert "ref_obs_digest" in normalized and normalized.get("ref_obs_digest") is None

    assert (
        ref_obs_digest_consistency_decision(normalized, current_obs_digest="deadbeef")
        == "not_applicable"
    )


def test_ref_checks_apply_when_observation_present(tmp_path: Path) -> None:
    writer = EvidenceWriter(
        run_dir=tmp_path, case_id="case_ingest_has_obs", seed=0, run_mode="phase3"
    )
    writer.record_observation(
        step=0,
        observation={
            "screenshot_png": _TINY_PNG_1X1,
            "screen_info": {
                "width_px": 100,
                "height_px": 200,
                "density_dpi": 320,
                "surface_orientation": 0,
            },
            "foreground": {"package": "com.example", "activity": "MainActivity"},
            "a11y_tree": {"nodes": [{"id": "root", "role": "window", "children": []}]},
        },
    )
    current = writer.last_obs_digest
    normalized = writer.record_agent_action(step=0, action={"type": "tap", "x": 1, "y": 2})
    writer.close()

    assert isinstance(current, str) and current
    assert normalized.get("auditability_limited") is False

    assert normalized.get("ref_check_applicable") is True
    assert normalized.get("ref_obs_digest") == current
    assert normalized.get("obs_digest") == current

    assert ref_obs_digest_consistency_decision(normalized, current_obs_digest=current) == "pass"
