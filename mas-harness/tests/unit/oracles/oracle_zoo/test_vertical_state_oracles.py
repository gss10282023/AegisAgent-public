from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.dumpsys.bluetooth import BluetoothOracle
from mas_harness.oracles.zoo.dumpsys.connectivity import (
    ConnectivityOracle,
    parse_connectivity,
)
from mas_harness.oracles.zoo.dumpsys.location import LocationOracle
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256


@dataclass(frozen=True)
class FakeAdbResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int


class FakeController:
    def __init__(
        self,
        *,
        settings: dict[tuple[str, str], str] | None = None,
        dumpsys: dict[str, str] | None = None,
        serial: str = "FAKE_SERIAL",
    ) -> None:
        self.serial = serial
        self._settings = dict(settings or {})
        self._dumpsys = dict(dumpsys or {})

    def adb_shell(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        timeout_ms: int | None = None,
        check: bool = True,
    ) -> FakeAdbResult:
        _ = timeout_s, timeout_ms, check
        cmd = str(command).strip()

        if cmd.startswith("settings "):
            parts = shlex.split(cmd)
            if len(parts) >= 4 and parts[1] == "get":
                namespace, key = parts[2], parts[3]
                value = self._settings.get((namespace, key), "null")
                return FakeAdbResult(
                    args=["adb", "shell", cmd],
                    stdout=f"{value}\n",
                    stderr="",
                    returncode=0,
                )

        if cmd.startswith("dumpsys "):
            service = cmd.split()[1]
            stdout = self._dumpsys.get(service, "")
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(stdout),
                stderr="",
                returncode=0,
            )

        return FakeAdbResult(args=["adb", "shell", cmd], stdout="", stderr="", returncode=0)


def _fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ctx(*, controller: Any, episode_dir: Path) -> OracleContext:
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_dir=episode_dir
    )


def test_parse_connectivity_extracts_active_transport() -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    stdout = _fixture(tests_dir / "fixtures" / "dumpsys_connectivity_wifi_validated.txt")

    parsed = parse_connectivity(stdout)
    assert parsed["ok"] is True
    assert parsed["active_net_id"] == 100
    active = parsed["active_network"]
    assert isinstance(active, dict)
    assert active["transport"] == "WIFI"
    assert active["validated"] is True
    assert active["connected"] is True


def test_connectivity_oracle_matches_expected_fields_and_writes_artifact(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    connectivity = _fixture(tests_dir / "fixtures" / "dumpsys_connectivity_wifi_validated.txt")

    controller = FakeController(
        settings={
            ("global", "airplane_mode_on"): "0",
            ("global", "wifi_on"): "1",
        },
        dumpsys={"connectivity": connectivity},
    )
    oracle = ConnectivityOracle(
        airplane_mode=False,
        wifi_enabled=True,
        active_transport="WIFI",
        validated=True,
        timeout_ms=50,
    )
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    assert len(evidence) == 1
    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True

    artifacts = evidence[0].get("artifacts")
    assert isinstance(artifacts, list) and artifacts
    artifact = artifacts[0]
    path = tmp_path / str(artifact["path"])
    assert path.exists()
    assert artifact["sha256"] == stable_file_sha256(path)


def test_connectivity_oracle_negative_wrong_active_transport(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    connectivity = _fixture(tests_dir / "fixtures" / "dumpsys_connectivity_wifi_validated.txt")

    controller = FakeController(
        settings={("global", "airplane_mode_on"): "0", ("global", "wifi_on"): "1"},
        dumpsys={"connectivity": connectivity},
    )
    oracle = ConnectivityOracle(active_transport="CELLULAR", timeout_ms=50)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is False


def test_location_oracle_matches_enabled_and_mode(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    dumpsys_location = _fixture(tests_dir / "fixtures" / "dumpsys_location_enabled_true.txt")

    controller = FakeController(
        settings={("secure", "location_mode"): "3"},
        dumpsys={"location": dumpsys_location},
    )
    oracle = LocationOracle(enabled=True, mode="HIGH_ACCURACY", timeout_ms=50)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True


def test_location_oracle_negative_disabled_is_conclusive_failure(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    dumpsys_location = _fixture(tests_dir / "fixtures" / "dumpsys_location_enabled_false.txt")

    controller = FakeController(
        settings={("secure", "location_mode"): "0"},
        dumpsys={"location": dumpsys_location},
    )
    oracle = LocationOracle(enabled=True, timeout_ms=50)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is False


def test_bluetooth_oracle_matches_enabled(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    dumpsys_bt = _fixture(tests_dir / "fixtures" / "dumpsys_bluetooth_manager_enabled_true.txt")

    controller = FakeController(
        settings={("global", "bluetooth_on"): "1"},
        dumpsys={"bluetooth_manager": dumpsys_bt},
    )
    oracle = BluetoothOracle(enabled=True, timeout_ms=50)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is True


def test_bluetooth_oracle_negative_mismatch(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    dumpsys_bt = _fixture(tests_dir / "fixtures" / "dumpsys_bluetooth_manager_enabled_false.txt")

    controller = FakeController(
        settings={("global", "bluetooth_on"): "0"},
        dumpsys={"bluetooth_manager": dumpsys_bt},
    )
    oracle = BluetoothOracle(enabled=True, timeout_ms=50)
    evidence = oracle.post_check(_ctx(controller=controller, episode_dir=tmp_path))

    decision = decision_from_evidence(evidence, oracle_id=oracle.oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is False
