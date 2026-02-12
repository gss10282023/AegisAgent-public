from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, runtime_checkable

RawAgentEvent = Dict[str, Any]


class ActionEvidenceError(RuntimeError):
    pass


class ActionEvidenceSpecError(ActionEvidenceError):
    pass


class ActionEvidenceCollectionError(ActionEvidenceError):
    pass


def _require_nonempty_str(obj: Mapping[str, Any], key: str, *, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ActionEvidenceSpecError(f"{where}.{key} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class EventStreamSpec:
    format: str
    path_on_host: str


@dataclass(frozen=True)
class ActionEvidenceSpec:
    level: str
    source: str
    event_stream: EventStreamSpec


def parse_action_evidence_spec(
    manifest: Mapping[str, Any],
    *,
    where: str = "adapter_manifest",
) -> ActionEvidenceSpec | None:
    action_evidence = manifest.get("action_evidence")
    if action_evidence is None:
        return None
    if not isinstance(action_evidence, Mapping):
        raise ActionEvidenceSpecError(f"{where}.action_evidence must be an object")

    level = _require_nonempty_str(action_evidence, "level", where=f"{where}.action_evidence")
    source = _require_nonempty_str(action_evidence, "source", where=f"{where}.action_evidence")

    stream = action_evidence.get("event_stream")
    if not isinstance(stream, Mapping):
        raise ActionEvidenceSpecError(f"{where}.action_evidence.event_stream must be an object")

    fmt = _require_nonempty_str(stream, "format", where=f"{where}.action_evidence.event_stream")
    path_on_host = _require_nonempty_str(
        stream, "path_on_host", where=f"{where}.action_evidence.event_stream"
    )

    return ActionEvidenceSpec(
        level=level,
        source=source,
        event_stream=EventStreamSpec(format=fmt, path_on_host=path_on_host),
    )


def resolve_path_on_host(
    path_on_host: str,
    *,
    repo_root: Optional[Path] = None,
    run_dir: Optional[Path] = None,
) -> Path:
    """Resolve a collector path in a flexible way.

    - If absolute: used as-is.
    - If relative:
        * Prefer an existing file under run_dir (if provided).
        * Then prefer an existing file under repo_root (if provided).
        * Otherwise fall back to repo_root-relative (if provided), else cwd-relative.
    """

    raw = str(path_on_host).strip()
    if not raw:
        raise ActionEvidenceCollectionError("path_on_host must be non-empty")

    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate

    if run_dir is not None:
        p = (run_dir / candidate).resolve()
        if p.exists():
            return p

    if repo_root is not None:
        p = (repo_root / candidate).resolve()
        if p.exists():
            return p

    if repo_root is not None:
        return (repo_root / candidate).resolve()
    return candidate.resolve()


@runtime_checkable
class ActionEvidenceCollector(Protocol):
    def collect(self) -> list[RawAgentEvent]: ...
