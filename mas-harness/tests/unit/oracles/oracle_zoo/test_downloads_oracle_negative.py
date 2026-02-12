from __future__ import annotations

from pathlib import Path

from mas_harness.oracles.zoo.base import assert_oracle_event_v0, decision_from_evidence
from mas_harness.oracles.zoo.providers.downloads import DownloadManagerOracle

from .fakes import FakeController, make_ctx


def _decision(*, token: str, stdout: str, t0: int, now: int, tmp_path: Path) -> dict:
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://downloads/my_downloads", None): stdout,
        },
    )
    oracle = DownloadManagerOracle(token=token)
    ctx = make_ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)
    evidence = oracle.post_check(ctx)
    for ev in evidence:
        assert_oracle_event_v0(ev)
    return decision_from_evidence(evidence, oracle_id=oracle.oracle_id)


def test_downloads_oracle_negative_status_not_success(tmp_path: Path) -> None:
    t0 = 1_700_000_000_000
    now = t0 + 10_000
    token = "DL_TOKEN"
    stdout = (
        "Row: 0 _id=42, status=400, title=report_{token}.pdf, uri=https://ex.com/{token}.pdf, "
        "local_filename=/sdcard/Download/report_{token}.pdf, lastmod=1700000005000\n"
    ).format(token=token)

    decision = _decision(token=token, stdout=stdout, t0=t0, now=now, tmp_path=tmp_path)
    assert decision["conclusive"] is True
    assert decision["success"] is False


def test_downloads_oracle_negative_window_mismatch(tmp_path: Path) -> None:
    t0 = 1_700_000_000_000
    now = t0 + 10_000
    token = "DL_TOKEN"
    stdout = (
        "Row: 0 _id=42, status=200, title=report_{token}.pdf, uri=https://ex.com/{token}.pdf, "
        "local_filename=/sdcard/Download/report_{token}.pdf, lastmod=1690000000000\n"
    ).format(token=token)

    decision = _decision(token=token, stdout=stdout, t0=t0, now=now, tmp_path=tmp_path)
    assert decision["conclusive"] is True
    assert decision["success"] is False


def test_downloads_oracle_negative_token_mismatch(tmp_path: Path) -> None:
    t0 = 1_700_000_000_000
    now = t0 + 10_000
    token = "DL_TOKEN"
    stdout = (
        "Row: 0 _id=42, status=200, title=report.pdf, uri=https://ex.com/report.pdf, "
        "local_filename=/sdcard/Download/report.pdf, lastmod=1700000005000\n"
    )

    decision = _decision(token=token, stdout=stdout, t0=t0, now=now, tmp_path=tmp_path)
    assert decision["conclusive"] is True
    assert decision["success"] is False
