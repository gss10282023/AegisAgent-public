from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256
from mas_harness.oracles.zoo.utils.time_window import EpisodeTime, TimeWindow
from mas_harness.oracles.zoo.utils.ui_token_match import UiTokenOracle, scan_ui_elements_jsonl


@dataclass(frozen=True)
class FakeController:
    serial: str = "FAKE_SERIAL"


def _fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ctx(*, controller: Any, episode_dir: Path) -> OracleContext:
    episode_time = EpisodeTime(t0_host_utc_ms=0, t0_device_epoch_ms=None, slack_ms=0)
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=episode_time, episode_dir=episode_dir
    )


def test_ui_token_match_text_and_resource_id(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    sample = _fixture(tests_dir / "fixtures" / "ui_elements_token_sample.jsonl")
    ui_path = tmp_path / "ui_elements.jsonl"
    ui_path.write_text(sample, encoding="utf-8")

    host_window = TimeWindow(t0_ms=0, now_ms=0, slack_ms=0, start_ms=0, end_ms=10)

    scan_text = scan_ui_elements_jsonl(
        path=ui_path,
        token="Sign in",
        token_match="contains",
        fields=("text", "resource_id"),
        package=None,
        host_window=host_window,
    )
    assert scan_text["ok"] is True
    assert scan_text["matches"]
    assert scan_text["matches"][0]["field"] == "text"

    scan_rid = scan_ui_elements_jsonl(
        path=ui_path,
        token="com.example:id/login_button",
        token_match="equals",
        fields=("resource_id",),
        package="com.example.app",
        host_window=host_window,
    )
    assert scan_rid["ok"] is True
    assert scan_rid["matches"]
    assert scan_rid["matches"][0]["field"] == "resource_id"


def test_ui_token_oracle_matches(tmp_path: Path) -> None:
    tests_dir = Path(__file__).resolve().parents[3]
    sample = _fixture(tests_dir / "fixtures" / "ui_elements_token_sample.jsonl")
    (tmp_path / "ui_elements.jsonl").write_text(sample, encoding="utf-8")

    oracle = UiTokenOracle(token="Sign in", token_match="contains", fields=("text", "resource_id"))
    ctx = _ctx(controller=FakeController(), episode_dir=tmp_path)
    evidence = oracle.post_check(ctx)

    assert_oracle_event_v0(evidence[0])
    decision = decision_from_evidence(evidence, oracle_id="ui_token")
    assert decision["conclusive"] is True
    assert decision["success"] is True

    artifacts = evidence[0].get("artifacts")
    assert isinstance(artifacts, list) and artifacts
    artifact = artifacts[0]
    path = tmp_path / str(artifact["path"])
    assert path.exists()
    assert artifact["sha256"] == stable_file_sha256(path)


def test_ui_token_oracle_inconclusive_on_unknown_format(tmp_path: Path) -> None:
    (tmp_path / "ui_elements.jsonl").write_text("not json\n", encoding="utf-8")

    oracle = UiTokenOracle(token="Sign in")
    ctx = _ctx(controller=FakeController(), episode_dir=tmp_path)
    evidence = oracle.post_check(ctx)

    decision = decision_from_evidence(evidence, oracle_id="ui_token")
    assert decision["conclusive"] is False
    assert decision["success"] is False
    assert "parse" in decision["reason"].lower()
