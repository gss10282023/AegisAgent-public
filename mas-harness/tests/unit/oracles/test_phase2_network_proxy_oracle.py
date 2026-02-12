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


class ProxyLogWriterEnv(ToyEnv):
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


def _jsonl_line(obj: Dict[str, object]) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def test_network_proxy_oracle_success_matches_token_and_run_id_and_redacts(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    token = "NET_TOKEN_PROXY_1"
    run_id = "RUN_1"

    def _emit_proxy_log(action: Dict[str, Any]) -> None:
        if action.get("type") != "wait":
            return
        now_ms = int(time.time() * 1000)
        log_path = artifacts_root / "proxy_log.jsonl"
        wrong = {
            "ts_ms": now_ms,
            "request": {
                "method": "POST",
                "host": "example.com",
                "path": "/api/submit",
                "body_sha256": "a" * 64,
            },
            "response": {"status_code": 200},
            "token_sha256": stable_sha256("WRONG"),
            "run_id_sha256": stable_sha256(run_id),
        }
        right = {
            "ts_ms": now_ms + 1,
            "request": {
                "method": "POST",
                "host": "example.com",
                "path": "/api/submit",
                "body_sha256": "b" * 64,
            },
            "response": {"status_code": 200},
            "token_sha256": stable_sha256(token),
            "run_id_sha256": stable_sha256(run_id),
        }
        log_path.write_text(_jsonl_line(wrong) + "\n" + _jsonl_line(right) + "\n", encoding="utf-8")

    env = ProxyLogWriterEnv(on_execute=_emit_proxy_log)

    case_id = "network_proxy_success"
    case_dir = tmp_path / "case_network_proxy_success"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: {case_id}
            user_goal: "[TEST] Network proxy oracle success"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_proxy
              enabled: true
              path: proxy_log.jsonl
              clear_before_run: true
              clear_mode: truncate
              token: {token}
              run_id: {run_id}
              method: POST
              host: example.com
              path_match: "equals:/api/submit"
              status_min: 200
              status_max: 399
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
    events = [
        json.loads(line)
        for line in (episode_dir / "oracle_trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    post = [e for e in events if e.get("oracle_id") == "network_proxy" and e.get("phase") == "post"]
    assert len(post) == 1
    event = post[0]
    preview = event.get("result_preview")
    assert isinstance(preview, dict)
    matched = preview.get("matched_event")
    assert isinstance(matched, dict)
    assert matched["request"]["method"] == "POST"
    assert matched["request"]["host"] == "example.com"
    assert matched["request"]["path"] == "/api/submit"
    assert matched["response"]["status_code"] == 200
    assert matched["token_sha256"] == stable_sha256(token)
    assert matched["run_id_sha256"] == stable_sha256(run_id)

    artifacts = event.get("artifacts")
    assert isinstance(artifacts, list) and len(artifacts) == 1
    artifact_path = episode_dir / str(artifacts[0]["path"])
    assert artifact_path.exists()
    assert token not in artifact_path.read_text(encoding="utf-8")


def test_network_proxy_oracle_token_mismatch_fails(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    def _emit_wrong_token(action: Dict[str, Any]) -> None:
        if action.get("type") != "wait":
            return
        now_ms = int(time.time() * 1000)
        (artifacts_root / "proxy_log.jsonl").write_text(
            _jsonl_line(
                {
                    "ts_ms": now_ms,
                    "request": {
                        "method": "POST",
                        "host": "example.com",
                        "path": "/api/submit",
                        "body_sha256": "a" * 64,
                    },
                    "response": {"status_code": 200},
                    "token_sha256": stable_sha256("WRONG"),
                }
            )
            + "\n",
            encoding="utf-8",
        )

    env = ProxyLogWriterEnv(on_execute=_emit_wrong_token)

    case_dir = tmp_path / "case_network_proxy_token_mismatch"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            """\
            task_id: network_proxy_token_mismatch
            user_goal: "[TEST] Network proxy oracle token mismatch"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_proxy
              enabled: true
              path: proxy_log.jsonl
              clear_before_run: true
              clear_mode: truncate
              token: RIGHT
              method: POST
              host: example.com
              path_match: "equals:/api/submit"
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


def test_network_proxy_oracle_run_id_mismatch_fails(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    token = "NET_TOKEN_PROXY_RUNID"

    def _emit_wrong_run_id(action: Dict[str, Any]) -> None:
        if action.get("type") != "wait":
            return
        now_ms = int(time.time() * 1000)
        (artifacts_root / "proxy_log.jsonl").write_text(
            _jsonl_line(
                {
                    "ts_ms": now_ms,
                    "request": {
                        "method": "POST",
                        "host": "example.com",
                        "path": "/api/submit",
                        "body_sha256": "a" * 64,
                    },
                    "response": {"status_code": 200},
                    "token_sha256": stable_sha256(token),
                    "run_id_sha256": stable_sha256("WRONG_RUN"),
                }
            )
            + "\n",
            encoding="utf-8",
        )

    env = ProxyLogWriterEnv(on_execute=_emit_wrong_run_id)

    case_id = "network_proxy_run_id_mismatch"
    case_dir = tmp_path / "case_network_proxy_run_id_mismatch"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: {case_id}
            user_goal: "[TEST] Network proxy oracle run_id mismatch"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_proxy
              enabled: true
              path: proxy_log.jsonl
              clear_before_run: true
              clear_mode: truncate
              token: {token}
              run_id: RIGHT_RUN
              method: POST
              host: example.com
              path_match: "equals:/api/submit"
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
    assert "run_id" in str(summary["task_success_details"]["reason"]).lower()


def test_network_proxy_oracle_precheck_truncate_prevents_slack_pollution(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    # Simulate a quick rerun: stale file is "historical" but still within slack.
    token = "STALE"
    now_ms = int(time.time() * 1000)
    log_path = artifacts_root / "proxy_log.jsonl"
    log_path.write_text(
        _jsonl_line(
            {
                "ts_ms": now_ms,
                "request": {
                    "method": "POST",
                    "host": "example.com",
                    "path": "/api/submit",
                    "body_sha256": "a" * 64,
                },
                "response": {"status_code": 200},
                "token_sha256": stable_sha256(token),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    case_pass = tmp_path / "case_network_proxy_pollution_pass"
    _write_min_case(
        case_pass,
        task_yaml=textwrap.dedent(
            """\
            task_id: network_proxy_pollution_pass
            user_goal: "[TEST] Network proxy polluted pass without clearing"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_proxy
              enabled: true
              path: proxy_log.jsonl
              clear_before_run: false
              token: STALE
              method: POST
              host: example.com
              path_match: "equals:/api/submit"
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

    case_fail = tmp_path / "case_network_proxy_pollution_cleared"
    _write_min_case(
        case_fail,
        task_yaml=textwrap.dedent(
            """\
            task_id: network_proxy_pollution_cleared
            user_goal: "[TEST] Network proxy pre_check truncates stale log"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_proxy
              enabled: true
              path: proxy_log.jsonl
              clear_before_run: true
              clear_mode: truncate
              token: STALE
              method: POST
              host: example.com
              path_match: "equals:/api/submit"
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
    assert log_path.exists() is True
    assert log_path.read_text(encoding="utf-8") == ""


def test_network_proxy_oracle_stale_file_outside_window_fails(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACTS_ROOT", str(artifacts_root))

    token = "RIGHT"

    def _emit_stale(action: Dict[str, Any]) -> None:
        if action.get("type") != "wait":
            return
        now_ms = int(time.time() * 1000)
        log_path = artifacts_root / "proxy_log.jsonl"
        log_path.write_text(
            _jsonl_line(
                {
                    "ts_ms": now_ms,
                    "request": {
                        "method": "POST",
                        "host": "example.com",
                        "path": "/api/submit",
                        "body_sha256": "a" * 64,
                    },
                    "response": {"status_code": 200},
                    "token_sha256": stable_sha256(token),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        old = time.time() - 100.0
        os.utime(log_path, (old, old))

    env = ProxyLogWriterEnv(on_execute=_emit_stale)

    case_id = "network_proxy_stale"
    case_dir = tmp_path / "case_network_proxy_stale"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            f"""\
            task_id: {case_id}
            user_goal: "[TEST] Network proxy oracle stale file"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            time_window:
              slack_ms: 5000
            success_oracle:
              type: network_proxy
              enabled: true
              path: proxy_log.jsonl
              clear_before_run: true
              clear_mode: truncate
              token: {token}
              method: POST
              host: example.com
              path_match: "equals:/api/submit"
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
