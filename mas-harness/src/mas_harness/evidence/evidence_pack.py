"""Evidence Pack v0 contract (Phase 2 Step 2).

This module is the single source of truth for which files/dirs must exist for
every run and every episode bundle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

EVIDENCE_PACK_V0_RUN_REQUIRED_FILES: Tuple[str, ...] = (
    "run_manifest.json",
    "env_capabilities.json",
)

EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON: Tuple[str, ...] = ("summary.json",)

DEVICE_INPUT_TRACE_JSONL = "device_input_trace.jsonl"

EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSONL: Tuple[str, ...] = (
    "oracle_trace.jsonl",
    "action_trace.jsonl",
    "obs_trace.jsonl",
    "foreground_trace.jsonl",
    "device_trace.jsonl",
    "screen_trace.jsonl",
    "agent_call_trace.jsonl",
    "agent_action_trace.jsonl",
    "ui_elements.jsonl",
)

EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS: Tuple[str, ...] = (
    "ui_dump",
    "screenshots",
)

FACTS_JSONL = "facts.jsonl"
ASSERTIONS_JSONL = "assertions.jsonl"


class FactsJsonlParseError(ValueError):
    """Raised when a facts.jsonl JSONL line is invalid JSON or not an object."""


class AssertionsJsonlParseError(ValueError):
    """Raised when an assertions.jsonl JSONL line is invalid JSON or not an object."""


def evidence_pack_v0_episode_required_files_jsonl(
    *, action_trace_level: str | None = None
) -> Tuple[str, ...]:
    """Return the required JSONL filenames for an episode evidence dir.

    Phase 3 (3b-6b): when run_manifest.action_trace_level is L0/L1/L2, we require
    device_input_trace.jsonl to exist. When action_trace_level is "none" (or unknown),
    the file is optional.
    """

    required = list(EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSONL)
    if (
        str(action_trace_level or "").strip().upper() in {"L0", "L1", "L2"}
        and DEVICE_INPUT_TRACE_JSONL not in required
    ):
        required.append(DEVICE_INPUT_TRACE_JSONL)
    return tuple(required)


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def ensure_json_file(path: Path, *, default: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text(_json_dumps_canonical(default) + "\n", encoding="utf-8")


def ensure_jsonl_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text("", encoding="utf-8")


def ensure_evidence_pack_v0_episode_dir(
    episode_dir: Path, *, action_trace_level: str | None = None
) -> None:
    """Create the required Evidence Pack v0 structure for one episode bundle."""

    episode_dir.mkdir(parents=True, exist_ok=True)

    for d in EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS:
        (episode_dir / d).mkdir(parents=True, exist_ok=True)

    for name in evidence_pack_v0_episode_required_files_jsonl(
        action_trace_level=action_trace_level
    ):
        ensure_jsonl_file(episode_dir / name)

    for name in EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON:
        ensure_json_file(episode_dir / name, default={})


def resolve_episode_evidence_dir(episode_dir: Path) -> Path:
    """Return the concrete evidence dir for Phase2/Phase3 layouts."""

    episode_dir = Path(episode_dir)
    evidence_dir = episode_dir / "evidence"
    if (evidence_dir / "summary.json").is_file():
        return evidence_dir
    return episode_dir


def _iter_jsonl_objects(
    path: Path,
    *,
    source: str,
    parse_error: type[ValueError],
) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception as e:
            raise parse_error(f"{source}:{line_no}: invalid json ({e})") from e
        if not isinstance(obj, dict):
            raise parse_error(f"{source}:{line_no}: jsonl line must be an object")
        yield obj


def iter_facts(episode_dir: Path) -> Iterator[Dict[str, Any]]:
    """Iterate Facts from facts.jsonl (if present).

    If the file does not exist, returns an empty iterator.
    """

    evidence_dir = resolve_episode_evidence_dir(episode_dir)
    path = evidence_dir / FACTS_JSONL
    yield from _iter_jsonl_objects(path, source=str(path), parse_error=FactsJsonlParseError)


def iter_assertions(episode_dir: Path) -> Iterator[Dict[str, Any]]:
    """Iterate AssertionResults from assertions.jsonl (if present).

    If the file does not exist, returns an empty iterator.
    """

    evidence_dir = resolve_episode_evidence_dir(episode_dir)
    path = evidence_dir / ASSERTIONS_JSONL
    yield from _iter_jsonl_objects(path, source=str(path), parse_error=AssertionsJsonlParseError)
