from pathlib import Path

from mas_harness.cli.snapshot_leaderboard import parse_leaderboard_csv


def test_parse_leaderboard_csv_offline_fixture() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    fixture = repo_root / "mas-harness" / "tests" / "fixtures" / "leaderboard_sample.html"
    text = fixture.read_text(encoding="utf-8")

    entries = parse_leaderboard_csv(text)
    assert len(entries) == 3

    assert entries[0]["id"] == "agi-0"
    assert entries[0]["name"] == "AGI-0"
    assert entries[0]["open_status"] == "closed"
    assert entries[0]["link"] == "https://www.theagi.company/blog/android-world"

    assert entries[1]["id"] == "droidrun"
    assert entries[1]["name"] == "DroidRun"
    assert entries[1]["open_status"] == "open"
    assert entries[1]["link"] == "https://example.com/droidrun"

    assert entries[2]["id"] == "mystery-agent"
    assert entries[2]["name"] == "Mystery Agent"
    assert entries[2]["open_status"] == "unknown"
    assert entries[2]["link"] is None
