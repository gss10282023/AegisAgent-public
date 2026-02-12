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


def _load_summary(out_dir: Path) -> dict:
    return json.loads((out_dir / "episode_0000" / "summary.json").read_text(encoding="utf-8"))


def test_agentctl_fixed_writes_phase3_success_semantics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MAS_ANDROID_SERIAL", raising=False)
    monkeypatch.delenv("MAS_ADB_PATH", raising=False)

    out_dir = tmp_path / "agentctl_fixed_semantics"
    rc = _run_cli(["agentctl", "fixed", "--agent_id", "toy_agent", "--output", str(out_dir)])
    assert rc == 0

    summary = _load_summary(out_dir)
    for k in ("oracle_decision", "agent_reported_finished", "task_success", "run_purpose"):
        assert k in summary, f"missing {k} in episode summary.json"

    assert summary["run_purpose"] == "agentctl_fixed"
    assert summary["oracle_decision"] in {"pass", "fail", "inconclusive", "not_applicable"}
    assert isinstance(summary["agent_reported_finished"], bool)
    if summary["oracle_decision"] == "pass":
        assert summary["task_success"] is True
    elif summary["oracle_decision"] == "fail":
        assert summary["task_success"] is False
    else:
        assert summary["task_success"] == "unknown"


def test_agentctl_nl_writes_phase3_success_semantics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MAS_ANDROID_SERIAL", raising=False)
    monkeypatch.delenv("MAS_ADB_PATH", raising=False)

    out_dir = tmp_path / "agentctl_nl_semantics"
    rc = _run_cli(
        [
            "agentctl",
            "nl",
            "--agent_id",
            "toy_agent",
            "--goal",
            "打开设置",
            "--max_steps",
            "5",
            "--output",
            str(out_dir),
        ]
    )
    assert rc == 0

    summary = _load_summary(out_dir)
    for k in ("oracle_decision", "agent_reported_finished", "task_success", "run_purpose"):
        assert k in summary, f"missing {k} in episode summary.json"

    assert summary["run_purpose"] == "agentctl_nl"
    assert summary["oracle_decision"] == "not_applicable"
    assert isinstance(summary["agent_reported_finished"], bool)
    assert summary["task_success"] == "unknown"
