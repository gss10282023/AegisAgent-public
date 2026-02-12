from __future__ import annotations

import json
import os
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mas_harness.examples import ToyEnv
from mas_harness.oracles.zoo.utils.hashing import stable_sha256
from mas_harness.runtime.run_public import run_case


class ReceiptWriterEnv(ToyEnv):
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


def test_network_receipt_oracle_glob_picks_latest_and_matches_scoped_token(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    token = "NET_TOKEN_1"

    def _emit_two_receipts(action: Dict[str, Any]) -> None:
        if action.get("type") != "wait":
            return

        old_path = artifacts_root / "net_receipt_1.json"
        old_path.write_text(
            json.dumps(
                {
                    "request": {
                        "method": "POST",
                        "headers": {"X-Token": "OLD"},
                        "query": {},
                        "body": {"token": "OLD"},
                    }
                }
            ),
            encoding="utf-8",
        )
        old = time.time() - 1.0
        os.utime(old_path, (old, old))

        new_path = artifacts_root / "net_receipt_2.json"
        new_path.write_text(
            json.dumps(
                {
                    "request": {
                        "method": "POST",
                        "headers": {"X-Token": token},
                        "query": {"q": "x"},
                        "body": {"token": token},
                    }
                }
            ),
            encoding="utf-8",
        )

    env = ReceiptWriterEnv(on_execute=_emit_two_receipts)

    case_id = "network_receipt_glob_success"
    case_dir = tmp_path / "case_network_receipt_glob_success"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: {case_id}
            user_goal: "[TEST] Network receipt glob success"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_receipt
              glob: "net_receipt_*.json"
              clear_before_run: true
              token: {token}
              token_scopes: ["request.body", "request.headers", "request.query"]
              expected:
                request.method: "POST"
              steps: 1
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
        e for e in events if e.get("oracle_id") == "network_receipt" and e.get("phase") == "post"
    ]
    assert len(post) == 1
    event = post[0]

    preview = event.get("result_preview")
    assert isinstance(preview, dict)
    assert str(preview.get("path", "")).endswith("net_receipt_2.json")
    token_match = preview.get("token_match")
    assert isinstance(token_match, dict) and token_match.get("ok") is True
    assert token_match.get("token_sha256") == stable_sha256(token)
    assert preview.get("mismatches") == {}

    artifacts = event.get("artifacts")
    assert isinstance(artifacts, list) and len(artifacts) == 1
    artifact = artifacts[0]
    artifact_path = episode_dir / str(artifact.get("path"))
    assert artifact_path.exists()
    assert token not in artifact_path.read_text(encoding="utf-8")


def test_network_receipt_oracle_precheck_clears_slack_pollution(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    receipt = artifacts_root / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "request": {
                    "method": "POST",
                    "headers": {},
                    "query": {},
                    "body": {"token": "STALE"},
                }
            }
        ),
        encoding="utf-8",
    )

    case_pass = tmp_path / "case_network_receipt_pollution_pass"
    _write_min_case(
        case_pass,
        task_yaml=textwrap.dedent(
            """\
            task_id: network_receipt_pollution_pass
            user_goal: "[TEST] Network receipt polluted pass without clearing"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_receipt
              path: receipt.json
              token: STALE
              clear_before_run: false
              expected:
                request.method: "POST"
              steps: 1
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

    case_fail = tmp_path / "case_network_receipt_pollution_cleared"
    _write_min_case(
        case_fail,
        task_yaml=textwrap.dedent(
            """\
            task_id: network_receipt_pollution_cleared
            user_goal: "[TEST] Network receipt pre_check clears stale file"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_receipt
              path: receipt.json
              token: STALE
              clear_before_run: true
              expected:
                request.method: "POST"
              steps: 1
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
    assert summary_fail["status"] == "fail"
    assert receipt.exists() is False


def test_network_receipt_oracle_token_mismatch_fails(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    def _emit_wrong_token(action: Dict[str, Any]) -> None:
        if action.get("type") != "wait":
            return
        (artifacts_root / "receipt.json").write_text(
            json.dumps(
                {
                    "request": {
                        "method": "POST",
                        "headers": {"X-Token": "WRONG"},
                        "query": {},
                        "body": {"token": "WRONG"},
                    }
                }
            ),
            encoding="utf-8",
        )

    env = ReceiptWriterEnv(on_execute=_emit_wrong_token)

    case_dir = tmp_path / "case_network_receipt_token_mismatch"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            """\
            task_id: network_receipt_token_mismatch
            user_goal: "[TEST] Network receipt token mismatch"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_receipt
              path: receipt.json
              clear_before_run: true
              token: RIGHT
              token_scopes: ["request.body", "request.headers", "request.query"]
              expected:
                request.method: "POST"
              steps: 1
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
    assert summary["status"] == "fail"
    assert "token" in str(summary["task_success_details"]["reason"]).lower()


def test_network_receipt_oracle_stale_outside_time_window_fails(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    token = "RIGHT"

    def _emit_stale(action: Dict[str, Any]) -> None:
        if action.get("type") != "wait":
            return
        receipt = artifacts_root / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "request": {
                        "method": "POST",
                        "headers": {"X-Token": token},
                        "query": {},
                        "body": {"token": token},
                    }
                }
            ),
            encoding="utf-8",
        )
        old = time.time() - 100.0
        os.utime(receipt, (old, old))

    env = ReceiptWriterEnv(on_execute=_emit_stale)

    case_id = "network_receipt_stale"
    case_dir = tmp_path / "case_network_receipt_stale"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: {case_id}
            user_goal: "[TEST] Network receipt stale"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_receipt
              path: receipt.json
              clear_before_run: true
              token: {token}
              token_scopes: ["request.body", "request.headers", "request.query"]
              expected:
                request.method: "POST"
              steps: 1
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
    assert summary["status"] == "fail"
    assert "stale" in str(summary["task_success_details"]["reason"]).lower()
