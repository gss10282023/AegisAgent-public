from __future__ import annotations

import json
import os
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mas_harness.examples import ToyEnv
from mas_harness.runtime.run_public import run_case


class ArtifactWriterEnv(ToyEnv):
    def __init__(self, *, on_execute: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        super().__init__()
        self._on_execute = on_execute

    def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        result = super().execute(action)
        if self._on_execute is not None:
            self._on_execute(dict(action))
        return result


def _write_min_case(case_dir: Path, *, task_yaml: str) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "task.yaml").write_text(task_yaml, encoding="utf-8")
    (case_dir / "policy.yaml").write_text(
        textwrap.dedent(
            """\
            readable_set:
              ReadableApps: ["com.toy.app"]
              ReadableDataTypes: []
              ReadableWebOrigins: []

            writable_set:
              WritableApps: ["com.toy.app"]
              WritableSinks: ["wait"]
              WritableWebOrigins: []

            flow_rules: []
            high_risk_actions: []

            confirm_protocol:
              type: handshake

            budgets:
              max_steps: 1
              max_seconds: 30

            binding_requirements: []

            forbid_install: true
            install_allowlist: []
            forbid_settings_change:
              fields:
                - global:adb_enabled
                - global:airplane_mode_on
                - global:install_non_market_apps
                - global:stay_on_while_plugged_in
                - secure:accessibility_enabled
                - secure:enabled_accessibility_services
                - secure:location_mode
            """
        ),
        encoding="utf-8",
    )
    (case_dir / "eval.yaml").write_text(
        textwrap.dedent(
            """\
            oracle_evidence_required: true
            evidence_required:
              - action_trace
              - obs_trace
              - foreground_trace
              - ui_events
              - oracle_trace
              - summary
            checkers_enabled: []
            metrics_required:
              - BSR
              - steps
            """
        ),
        encoding="utf-8",
    )


def test_host_artifact_oracle_glob_picks_latest_and_records_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    # Create a "polluted" file that still falls inside the slack window.
    old_path = artifacts_root / "callback_old.json"
    old_path.write_text(
        json.dumps({"ok": True, "token": "OLD", "nested": {"id": 1}}),
        encoding="utf-8",
    )
    old = time.time() - 2.0
    os.utime(old_path, (old, old))

    token = "NEW"

    def _emit_new_artifact(action: Dict[str, Any]) -> None:
        if action.get("type") != "wait":
            return
        new_path = artifacts_root / "callback_new.json"
        new_path.write_text(
            json.dumps({"ok": True, "token": token, "nested": {"id": 2}}),
            encoding="utf-8",
        )

    env = ArtifactWriterEnv(on_execute=_emit_new_artifact)

    case_id = "host_artifact_glob_success"
    case_dir = tmp_path / "case_host_artifact_glob_success"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: {case_id}
            user_goal: "[TEST] Host artifact glob success"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: host_artifact_json
              glob: "callback_*.json"
              clear_before_run: false
              expected:
                ok: true
                token: {token}
                nested.id: 2
            """
        ),
    )

    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=env,
    )
    assert summary["status"] == "success"

    episode_dir = tmp_path / "public" / case_id / "seed_0"
    oracle_trace = episode_dir / "oracle_trace.jsonl"
    events = [
        json.loads(line)
        for line in oracle_trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    post = [
        e for e in events if e.get("oracle_id") == "host_artifact_json" and e.get("phase") == "post"
    ]
    assert len(post) == 1
    event = post[0]

    preview = event.get("result_preview")
    assert isinstance(preview, dict)
    assert str(preview.get("path", "")).endswith("callback_new.json")
    sha256 = preview.get("sha256")
    assert isinstance(sha256, str) and len(sha256) == 64
    assert preview.get("matched_fields", {}).get("token") == token
    assert preview.get("matched_fields", {}).get("nested.id") == 2
    assert preview.get("mismatches") == {}

    artifacts = event.get("artifacts")
    assert isinstance(artifacts, list) and len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.get("sha256") == sha256
    assert (episode_dir / str(artifact.get("path"))).exists()


def test_host_artifact_oracle_precheck_prevents_slack_pollution(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    # Simulate a quick rerun: stale file is "historical" but still within slack.
    receipt = artifacts_root / "receipt.json"
    receipt.write_text(json.dumps({"ok": True, "token": "STALE"}), encoding="utf-8")

    case_pass = tmp_path / "case_host_artifact_pollution_pass"
    _write_min_case(
        case_pass,
        task_yaml=textwrap.dedent(
            """\
            task_id: host_artifact_pollution_pass
            user_goal: "[TEST] Host artifact polluted pass without clearing"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: host_artifact_json
              path: receipt.json
              expected:
                ok: true
                token: STALE
              clear_before_run: false
            """
        ),
    )
    summary_pass = run_case(
        case_dir=case_pass,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=ToyEnv(),
    )
    assert summary_pass["status"] == "success"
    assert receipt.exists() is True

    case_fail = tmp_path / "case_host_artifact_pollution_cleared"
    _write_min_case(
        case_fail,
        task_yaml=textwrap.dedent(
            """\
            task_id: host_artifact_pollution_cleared
            user_goal: "[TEST] Host artifact pre_check clears stale file"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: host_artifact_json
              path: receipt.json
              expected:
                ok: true
                token: STALE
              clear_before_run: true
            """
        ),
    )
    summary_fail = run_case(
        case_dir=case_fail,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=ToyEnv(),
    )
    assert summary_fail["task_success_details"]["success"] is False
    assert receipt.exists() is False
