from __future__ import annotations

from pathlib import Path
from typing import Any

from mas_harness.oracles.zoo.base import assert_oracle_event_v0, decision_from_evidence
from mas_harness.oracles.zoo.providers.downloads import DownloadManagerOracle

from .fakes import FakeController, make_ctx


def test_downloads_oracle_parses_and_matches_success_row(tmp_path: Path) -> None:
    t0 = 1_700_000_000_000
    now = t0 + 10_000
    token = "DL_TOKEN"
    pkg = "com.example.downloader"

    stdout = (
        "Row: 0 _id=42, status=200, title=report_{token}.pdf, uri=https://ex.com/{token}.pdf, "
        "local_filename=/sdcard/Download/report_{token}.pdf, lastmod=1700000005000, "
        "notificationpackage={pkg}\n"
    ).format(token=token, pkg=pkg)

    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://downloads/my_downloads", None): stdout,
        },
    )

    oracle = DownloadManagerOracle(token=token, package=pkg)
    ctx = make_ctx(controller=controller, t0_device_ms=t0, episode_dir=tmp_path)

    evidence1 = oracle.post_check(ctx)
    for ev in evidence1:
        assert_oracle_event_v0(ev)
    decision1 = decision_from_evidence(evidence1, oracle_id=oracle.oracle_id)
    assert decision1["conclusive"] is True
    assert decision1["success"] is True

    # Stable digest for identical fixtures.
    evidence2 = oracle.post_check(ctx)
    assert evidence1[0]["result_digest"] == evidence2[0]["result_digest"]

    # Raw output persisted as artifact(s).
    event: dict[str, Any] = evidence1[0]
    artifacts = event.get("artifacts")
    assert isinstance(artifacts, list) and artifacts
    paths = {a.get("path") for a in artifacts if isinstance(a, dict)}
    assert "oracle/raw/content_query_my_downloads_post.txt" in paths
    assert (tmp_path / "oracle/raw/content_query_my_downloads_post.txt").exists()

    # Queries record full `adb shell content query` command.
    queries = event.get("queries")
    assert isinstance(queries, list) and queries
    assert any(
        isinstance(q, dict) and str(q.get("cmd", "")).startswith("shell content query")
        for q in queries
    )
