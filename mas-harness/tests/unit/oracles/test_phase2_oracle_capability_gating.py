from __future__ import annotations

import textwrap
from pathlib import Path

from mas_harness.runtime.run_public import run_case


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


def test_missing_controller_capability_maps_to_oracle_inconclusive(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    case_dir = tmp_path / "case_oracle_inconclusive"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            """\
            task_id: oracle_inconclusive_missing_adb_shell
            user_goal: "[TEST] Inconclusive when missing adb_shell"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            success_oracle:
              type: adb_shell_expect_regex
              shell_cmd: "echo ok"
              expect_regex: "ok"
              timeout_s: 1
            """
        ),
    )

    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
    )
    assert summary["status"] == "inconclusive"
    assert summary["failure_class"] == "oracle_inconclusive"
