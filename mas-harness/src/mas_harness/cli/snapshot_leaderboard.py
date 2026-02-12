from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import shutil
import ssl
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_ANDROIDWORLD_LEADERBOARD_SOURCE = (
    "https://docs.google.com/spreadsheets/d/"
    "1cchzP9dlTZ3WXQTfYNhh3avxoLipqHN75v1Tb86uhHo/export?format=csv&gid=0"
)
PARSER_VERSION = "androidworld_leaderboard_csv_v1"

_URL_RE = re.compile(r"https?://[^\\s\"'<>]+")


def _today_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _slugify(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = t.strip("-")
    return t or "unknown"


def _extract_first_url(text: str) -> Optional[str]:
    match = _URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(").,]")


def _normalize_header_cell(cell: str) -> str:
    return " ".join(cell.split()).strip().lower()


def _parse_float(text: str) -> Optional[float]:
    t = text.strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_int(text: str) -> Optional[int]:
    t = text.strip()
    if not t:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _open_status_from_marker(marker: str) -> str:
    t = marker.strip().lower()
    if t in {"✔", "✓", "yes", "y", "true", "open"}:
        return "open"
    if t in {"✗", "x", "no", "n", "false", "closed"}:
        return "closed"
    return "unknown"


def parse_leaderboard_csv(text: str) -> list[dict[str, Any]]:
    """Parse AndroidWorld leaderboard CSV export text into normalized entries.

    Expected source is the public Google Sheets CSV export linked from:
    https://github.com/google-research/android_world
    """
    rows = list(csv.reader(io.StringIO(text)))

    header_row: Optional[list[str]] = None
    header_idx: Optional[int] = None
    for i, row in enumerate(rows):
        if not row:
            continue
        if row[0].strip().lower() == "rank":
            header_row = row
            header_idx = i
            break

    if header_row is None or header_idx is None:
        raise ValueError("Could not locate leaderboard header row (expected first cell: 'Rank').")

    header_norm = [_normalize_header_cell(c) for c in header_row]
    index: dict[str, int] = {name: idx for idx, name in enumerate(header_norm) if name}

    def get_cell(row: list[str], name: str) -> str:
        idx = index.get(name)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in rows[header_idx + 1 :]:
        if not row or all(not c.strip() for c in row):
            continue

        rank = _parse_int(get_cell(row, "rank"))
        if rank is None:
            continue

        result_source = get_cell(row, "result source")
        model = get_cell(row, "model")
        name = result_source or model
        if not name:
            continue

        entry_id = _slugify(name)
        if entry_id in seen_ids:
            entry_id = f"{entry_id}__{rank}"
        seen_ids.add(entry_id)

        note = get_cell(row, "note")
        link = _extract_first_url(note) if note else None

        open_marker = get_cell(row, "open?")
        open_status = _open_status_from_marker(open_marker)

        entry: dict[str, Any] = {
            "id": entry_id,
            "name": name,
            "link": link,
            "open_status": open_status,
            "rank": rank,
        }

        release_date = get_cell(row, "release date")
        model_type = get_cell(row, "model type")
        model_size = get_cell(row, "model size")
        screen_representation = get_cell(row, "screen representation")
        success_rate_pass_at_1 = _parse_float(get_cell(row, "success rate (pass@1)"))
        number_of_trials = _parse_int(get_cell(row, "number of trials"))
        success_rate_pass_at_k = _parse_float(get_cell(row, "success rate (pass@k)"))
        trajectory_submissions = get_cell(row, "trajectory submissions")

        if release_date:
            entry["release_date"] = release_date
        if result_source:
            entry["result_source"] = result_source
        if model_type:
            entry["model_type"] = model_type
        if open_marker:
            entry["open_marker"] = open_marker
        if model_size:
            entry["model_size"] = model_size
        if model:
            entry["model"] = model
        if screen_representation:
            entry["screen_representation"] = screen_representation
        if success_rate_pass_at_1 is not None:
            entry["success_rate_pass_at_1"] = success_rate_pass_at_1
        if number_of_trials is not None:
            entry["number_of_trials"] = number_of_trials
        if success_rate_pass_at_k is not None:
            entry["success_rate_pass_at_k"] = success_rate_pass_at_k
        if trajectory_submissions:
            entry["trajectory_submissions"] = trajectory_submissions
        if note:
            entry["note"] = note

        entries.append(entry)

    return entries


def _fetch_url_bytes(source: str, *, insecure: bool) -> bytes:
    headers = {"User-Agent": "mas-harness/phase3-snapshot (https://github.com/openai/codex-cli)"}
    req = urllib.request.Request(source, headers=headers)

    ssl_ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()

    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            return resp.read()
    except Exception:
        curl = shutil.which("curl")
        if not curl:
            raise
        cmd = [curl, "-L", "-sS", source]
        if insecure:
            cmd.insert(1, "-k")
        res = subprocess.run(cmd, capture_output=True, check=True)
        return res.stdout


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot AndroidWorld leaderboard into a reproducible JSON file."
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path for leaderboard_snapshot.json",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help=(
            "Leaderboard source URL. Defaults to the official public Google Sheets CSV export when "
            "--infile is not set."
        ),
    )
    parser.add_argument(
        "--infile",
        type=Path,
        default=None,
        help="Optional local CSV export file (offline mode).",
    )
    parser.add_argument(
        "--snapshot_date",
        type=str,
        default=None,
        help="Override snapshot_date (YYYY-MM-DD). Defaults to UTC 'today'.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL verification when fetching remote sources.",
    )
    args = parser.parse_args()

    if args.infile is not None:
        raw = args.infile.read_bytes()
        source = args.source or str(args.infile)
    else:
        source = args.source or DEFAULT_ANDROIDWORLD_LEADERBOARD_SOURCE
        raw = _fetch_url_bytes(source, insecure=args.insecure)

    text = raw.decode("utf-8", errors="replace")
    entries = parse_leaderboard_csv(text)

    snapshot = {
        "snapshot_date": args.snapshot_date or _today_utc_iso(),
        "source": source,
        "parser_version": PARSER_VERSION,
        "entries": entries,
    }

    _write_json_atomic(args.out, snapshot)
    print(f"Wrote {len(entries)} entries -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
