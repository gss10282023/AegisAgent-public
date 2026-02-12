from __future__ import annotations

import json
import textwrap
from pathlib import Path
from types import SimpleNamespace

from mas_harness.examples import ToyEnv
from mas_harness.oracles.zoo.settings.boot_health import capture_device_infra
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


class FakeAdbToyEnv(ToyEnv):
    def __init__(
        self,
        *,
        boot_completed: str,
        airplane_mode_on: str = "0",
        auto_time: str = "1",
        timezone: str = "UTC",
        connectivity_dumpsys: str | None = None,
    ) -> None:
        super().__init__()
        self._boot_completed = boot_completed
        self._airplane_mode_on = airplane_mode_on
        self._auto_time = auto_time
        self._timezone = timezone
        self._connectivity_dumpsys = (
            connectivity_dumpsys
            if connectivity_dumpsys is not None
            else "Active default network: null\n"
        )

    def adb_shell(  # type: ignore[override]
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        timeout_ms: int | None = None,
        check: bool = True,
    ) -> SimpleNamespace:
        del timeout_s, timeout_ms, check

        stdout = ""
        rc = 0
        if command == "echo __mas_adb_ok__":
            stdout = "__mas_adb_ok__\n"
        elif command == "getprop sys.boot_completed":
            stdout = f"{self._boot_completed}\n"
        elif command.startswith("sh -c "):
            # sdcard writable probe: treat as writable
            stdout = ""
        elif command.startswith("rm -f "):
            stdout = ""
        elif command == "settings get global airplane_mode_on":
            stdout = f"{self._airplane_mode_on}\n"
        elif command == "settings get global auto_time":
            stdout = f"{self._auto_time}\n"
        elif command == "getprop persist.sys.timezone":
            stdout = f"{self._timezone}\n"
        elif command == "dumpsys connectivity":
            stdout = str(self._connectivity_dumpsys or "")
        elif command.startswith("date +%s%3N"):
            stdout = "1700000000000\n"
        elif command.startswith("date +%s"):
            stdout = "1700000000\n"

        return SimpleNamespace(
            args=["adb", "shell", command],
            returncode=rc,
            stdout=stdout,
            stderr="",
        )


def test_boot_not_completed_maps_to_infra_failed(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    schemas_dir = repo_root / "mas-spec" / "schemas"

    case_dir = tmp_path / "case_infra_failed_boot"
    _write_min_case(
        case_dir,
        task_yaml=textwrap.dedent(
            """\
            task_id: infra_failed_boot_not_completed
            user_goal: "[TEST] infra_failed when boot is not completed"
            interaction_mode: none
            initial_state: null
            max_steps: 1
            success_oracle:
              type: toy_success_after_steps
              steps: 0
            """
        ),
    )

    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=schemas_dir,
        controller=FakeAdbToyEnv(boot_completed="0"),
    )

    assert summary["status"] == "inconclusive"
    assert summary["failure_class"] == "infra_failed"
    assert "boot_not_completed" in summary["notes"]["infra"]["infra_failure_reasons"]

    device_trace = (
        tmp_path / "public" / "infra_failed_boot_not_completed" / "seed_0" / "device_trace.jsonl"
    )
    events = [
        json.loads(line)
        for line in device_trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    infra_event = next(ev for ev in events if ev.get("event") == "infra_probe")
    assert infra_event["boot_completed"] is False


def test_infra_probe_includes_network_airplane_auto_time() -> None:
    controller = FakeAdbToyEnv(
        boot_completed="1",
        airplane_mode_on="0",
        auto_time="1",
        timezone="Asia/Shanghai",
        connectivity_dumpsys=textwrap.dedent(
            "\n".join(
                [
                    "NetworkAgentInfo [WIFI () - 100] state: CONNECTED/VALIDATED",
                    (
                        "  NetworkCapabilities: [ NET_CAPABILITY_INTERNET&NET_CAPABILITY_VALIDATED "
                        "TRANSPORT_WIFI ]"
                    ),
                    "Active default network: 100",
                    "",
                ]
            )
        ),
    )

    infra_event, _ = capture_device_infra(controller, device_epoch_time_ms=1700000000000)
    assert infra_event["airplane_mode_on"] is False
    assert infra_event["auto_time"] is True
    assert infra_event["device_timezone"] == "Asia/Shanghai"
    assert infra_event["network"]["net_id"] == 100
    assert infra_event["network"]["transport"] == "wifi"
