from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from mas_harness.evidence.action_evidence.base import parse_action_evidence_spec
from mas_harness.integration.agents.adapter_manifest import load_adapter_manifest

AVAILABILITY_VALUES = ("runnable", "audit_only", "unavailable")
OPEN_STATUS_VALUES = ("open", "closed", "unknown")
TIER_VALUES = ("core", "extended")


class AgentRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistryIssue:
    code: str
    message: str
    agent_id: Optional[str] = None

    def format(self) -> str:
        if self.agent_id:
            return f"{self.code}: {self.agent_id}: {self.message}"
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class RegistryValidationReport:
    errors: list[RegistryIssue]
    warnings: list[RegistryIssue]


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _normalize_action_trace_level(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.lower() == "none":
        return "none"
    return s.upper()


def _normalize_tier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s:
        return None
    return s


def _resolve_path(repo_root: Path, raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (repo_root / p).resolve()


def discover_repo_root(start: Optional[Path] = None) -> Path:
    p = (start or Path(__file__)).resolve()
    for parent in (p, *p.parents):
        if (parent / "mas-agents" / "registry").is_dir():
            return parent
    return Path.cwd()


def default_env_profiles_dir() -> Path:
    return discover_repo_root() / "mas-agents" / "registry" / "env_profiles"


def load_env_profile(env_profiles_dir: Path, profile_id: str) -> dict[str, Any]:
    path = env_profiles_dir / f"{profile_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AgentRegistryError(f"env_profile must be a mapping: {path}")
    return data


def load_leaderboard_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AgentRegistryError(f"Snapshot must be an object: {path}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise AgentRegistryError(f"Snapshot must contain entries[]: {path}")
    return data


def load_agent_registry(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return []
    if not isinstance(data, list):
        raise AgentRegistryError(f"agent_registry.yaml top-level must be a list: {path}")

    entries: list[dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise AgentRegistryError(f"Registry entry #{idx} must be a mapping: {path}")
        entries.append(item)
    return entries


def validate_agent_registry(
    snapshot: dict[str, Any],
    registry_entries: list[dict[str, Any]],
    *,
    allow_extra_registry_entries: bool = True,
    enforce_snapshot_metadata_match: bool = False,
) -> RegistryValidationReport:
    errors: list[RegistryIssue] = []
    warnings: list[RegistryIssue] = []

    snapshot_entries = snapshot.get("entries")
    if not isinstance(snapshot_entries, list):
        return RegistryValidationReport(
            errors=[RegistryIssue("snapshot.invalid", "snapshot.entries must be a list")],
            warnings=[],
        )

    snapshot_by_id: dict[str, dict[str, Any]] = {}
    for idx, entry in enumerate(snapshot_entries):
        if not isinstance(entry, dict):
            errors.append(
                RegistryIssue("snapshot.invalid_entry", f"entries[{idx}] must be an object")
            )
            continue
        agent_id = entry.get("id")
        if not _nonempty_str(agent_id):
            errors.append(
                RegistryIssue("snapshot.missing_id", f"entries[{idx}].id must be a string")
            )
            continue
        agent_id = agent_id.strip()
        if agent_id in snapshot_by_id:
            errors.append(
                RegistryIssue("snapshot.duplicate_id", "duplicate snapshot id", agent_id=agent_id)
            )
            continue
        snapshot_by_id[agent_id] = entry

    registry_by_id: dict[str, dict[str, Any]] = {}
    for idx, entry in enumerate(registry_entries):
        agent_id = entry.get("agent_id")
        if not _nonempty_str(agent_id):
            errors.append(
                RegistryIssue(
                    "registry.missing_agent_id",
                    f"registry[{idx}].agent_id must be a non-empty string",
                )
            )
            continue

        agent_id = agent_id.strip()
        if agent_id in registry_by_id:
            errors.append(
                RegistryIssue(
                    "registry.duplicate_agent_id", "agent_id must be unique", agent_id=agent_id
                )
            )
            continue
        registry_by_id[agent_id] = entry

        agent_name = entry.get("agent_name")
        if not _nonempty_str(agent_name):
            errors.append(
                RegistryIssue(
                    "registry.missing_agent_name", "agent_name is required", agent_id=agent_id
                )
            )

        open_status = entry.get("open_status")
        if not _nonempty_str(open_status):
            errors.append(
                RegistryIssue(
                    "registry.missing_open_status", "open_status is required", agent_id=agent_id
                )
            )
        elif open_status not in OPEN_STATUS_VALUES:
            errors.append(
                RegistryIssue(
                    "registry.invalid_open_status",
                    f"open_status must be one of {sorted(OPEN_STATUS_VALUES)}",
                    agent_id=agent_id,
                )
            )

        availability = entry.get("availability")
        if not _nonempty_str(availability):
            errors.append(
                RegistryIssue(
                    "registry.missing_availability", "availability is required", agent_id=agent_id
                )
            )
            continue
        if availability not in AVAILABILITY_VALUES:
            errors.append(
                RegistryIssue(
                    "registry.invalid_availability",
                    f"availability must be one of {sorted(AVAILABILITY_VALUES)}",
                    agent_id=agent_id,
                )
            )
            continue

        if availability == "runnable":
            if not _nonempty_str(entry.get("adapter")):
                errors.append(
                    RegistryIssue(
                        "registry.missing_adapter",
                        "runnable entries must set adapter path",
                        agent_id=agent_id,
                    )
                )
        elif availability == "audit_only":
            has_ingest = _nonempty_str(entry.get("ingest"))
            has_fmt = _nonempty_str(entry.get("trajectory_format"))
            if not (has_ingest or has_fmt):
                errors.append(
                    RegistryIssue(
                        "registry.missing_ingest_or_trajectory_format",
                        "audit_only entries must set ingest or trajectory_format",
                        agent_id=agent_id,
                    )
                )
        elif availability == "unavailable":
            if not _nonempty_str(entry.get("unavailable_reason")):
                errors.append(
                    RegistryIssue(
                        "registry.missing_unavailable_reason",
                        "unavailable entries must set unavailable_reason",
                        agent_id=agent_id,
                    )
                )

        if agent_id in snapshot_by_id:
            snap_entry = snapshot_by_id[agent_id]
            if enforce_snapshot_metadata_match:
                mismatch_bucket = errors
                mismatch_code_prefix = "registry.snapshot_mismatch"
            else:
                mismatch_bucket = warnings
                mismatch_code_prefix = "registry.snapshot_mismatch_warning"

            snap_name = snap_entry.get("name")
            if (
                _nonempty_str(snap_name)
                and _nonempty_str(agent_name)
                and snap_name.strip() != agent_name.strip()
            ):
                mismatch_bucket.append(
                    RegistryIssue(
                        f"{mismatch_code_prefix}.agent_name",
                        f"agent_name differs from snapshot (snapshot={snap_name!r})",
                        agent_id=agent_id,
                    )
                )

            snap_open_status = snap_entry.get("open_status")
            if (
                _nonempty_str(snap_open_status)
                and _nonempty_str(open_status)
                and snap_open_status.strip() != open_status.strip()
            ):
                mismatch_bucket.append(
                    RegistryIssue(
                        f"{mismatch_code_prefix}.open_status",
                        f"open_status differs from snapshot (snapshot={snap_open_status!r})",
                        agent_id=agent_id,
                    )
                )

    missing_ids = sorted(set(snapshot_by_id) - set(registry_by_id))
    for agent_id in missing_ids:
        errors.append(
            RegistryIssue(
                "registry.missing_snapshot_entry",
                "snapshot entry missing in registry",
                agent_id=agent_id,
            )
        )

    extra_ids = sorted(set(registry_by_id) - set(snapshot_by_id))
    for agent_id in extra_ids:
        issue = RegistryIssue(
            "registry.extra_entry", "registry entry not present in snapshot", agent_id=agent_id
        )
        if allow_extra_registry_entries:
            warnings.append(issue)
        else:
            errors.append(issue)

    return RegistryValidationReport(errors=errors, warnings=warnings)


def validate_agent_registry_files(
    *,
    snapshot_path: Path,
    registry_path: Path,
    allow_extra_registry_entries: bool = True,
    enforce_snapshot_metadata_match: bool = False,
) -> RegistryValidationReport:
    snapshot = load_leaderboard_snapshot(snapshot_path)
    registry_entries = load_agent_registry(registry_path)
    return validate_agent_registry(
        snapshot,
        registry_entries,
        allow_extra_registry_entries=allow_extra_registry_entries,
        enforce_snapshot_metadata_match=enforce_snapshot_metadata_match,
    )


def validate_registry_manifest_consistency(
    registry_entries: list[dict[str, Any]],
    *,
    repo_root: Path | None = None,
) -> RegistryValidationReport:
    """Validate registry ↔ adapter_manifest consistency (Phase3 3b-7b).

    Rules:
    - registry action_trace_level L1 → manifest.action_evidence.level must be L1
    - registry action_trace_level L2 → manifest.action_evidence.level must be L2
    - manifest declaring action_evidence implies registry must match that level
    - L3 is forbidden (Phase3 does not produce L3)
    """

    errors: list[RegistryIssue] = []
    warnings: list[RegistryIssue] = []

    root = repo_root or discover_repo_root()

    for idx, entry in enumerate(registry_entries):
        if not isinstance(entry, dict):
            continue

        availability = str(entry.get("availability") or "").strip()
        if availability != "runnable":
            continue

        agent_id = str(entry.get("agent_id") or "").strip() or None
        action_trace_level = _normalize_action_trace_level(entry.get("action_trace_level"))

        if action_trace_level == "L3":
            errors.append(
                RegistryIssue(
                    "registry_manifest.forbidden_L3",
                    "action_trace_level=L3 is not supported in Phase3",
                    agent_id=agent_id,
                )
            )

        adapter_raw = entry.get("adapter")
        if not _nonempty_str(adapter_raw):
            errors.append(
                RegistryIssue(
                    "registry_manifest.missing_adapter",
                    f"runnable registry[{idx}].adapter must be a non-empty string",
                    agent_id=agent_id,
                )
            )
            continue

        adapter_path = _resolve_path(root, str(adapter_raw).strip())
        manifest_path = adapter_path.parent / "adapter_manifest.json"
        if not manifest_path.exists():
            errors.append(
                RegistryIssue(
                    "registry_manifest.missing_manifest",
                    f"missing adapter_manifest.json at {manifest_path}",
                    agent_id=agent_id,
                )
            )
            continue

        try:
            manifest = load_adapter_manifest(manifest_path)
        except Exception as e:
            errors.append(
                RegistryIssue(
                    "registry_manifest.invalid_manifest",
                    f"invalid adapter_manifest.json ({e})",
                    agent_id=agent_id,
                )
            )
            continue

        manifest_agent_id = str(manifest.get("agent_id") or "").strip() or None
        if agent_id and manifest_agent_id and manifest_agent_id != agent_id:
            errors.append(
                RegistryIssue(
                    "registry_manifest.agent_id_mismatch",
                    f"manifest.agent_id={manifest_agent_id!r} does not match registry agent_id",
                    agent_id=agent_id,
                )
            )

        try:
            spec = parse_action_evidence_spec(manifest)
        except Exception as e:
            errors.append(
                RegistryIssue(
                    "registry_manifest.invalid_action_evidence",
                    str(e),
                    agent_id=agent_id,
                )
            )
            continue

        manifest_level = spec.level.strip().upper() if spec is not None else None
        if manifest_level == "L3":
            errors.append(
                RegistryIssue(
                    "registry_manifest.forbidden_L3",
                    "manifest.action_evidence.level=L3 is not supported in Phase3",
                    agent_id=agent_id,
                )
            )

        if action_trace_level in {"L1", "L2"}:
            if spec is None:
                errors.append(
                    RegistryIssue(
                        "registry_manifest.missing_action_evidence",
                        f"registry action_trace_level={action_trace_level} "
                        "requires manifest.action_evidence",
                        agent_id=agent_id,
                    )
                )
            elif manifest_level != action_trace_level:
                errors.append(
                    RegistryIssue(
                        "registry_manifest.level_mismatch",
                        (
                            f"registry action_trace_level={action_trace_level} "
                            f"but manifest.action_evidence.level={manifest_level}"
                        ),
                        agent_id=agent_id,
                    )
                )
        else:
            if spec is not None:
                errors.append(
                    RegistryIssue(
                        "registry_manifest.level_mismatch",
                        (
                            f"registry action_trace_level={action_trace_level or 'none'} "
                            f"but manifest.action_evidence.level={manifest_level}"
                        ),
                        agent_id=agent_id,
                    )
                )

    return RegistryValidationReport(errors=errors, warnings=warnings)


def validate_core_agents(
    registry_entries: list[dict[str, Any]],
    *,
    repo_root: Path | None = None,
) -> RegistryValidationReport:
    """Validate Phase3 core/extended tier constraints (3b-8a).

    Hard rules for tier=core:
    - availability must be runnable (conformance must be runnable)
    - action_trace_level must be one of L0/L1/L2 (cannot be none)
    """

    errors: list[RegistryIssue] = []
    warnings: list[RegistryIssue] = []

    root = repo_root or discover_repo_root()
    core_count = 0

    for idx, entry in enumerate(registry_entries):
        if not isinstance(entry, dict):
            continue

        agent_id = entry.get("agent_id")
        agent_id_str = agent_id.strip() if _nonempty_str(agent_id) else None

        tier = _normalize_tier(entry.get("tier"))
        if tier is None:
            errors.append(
                RegistryIssue(
                    "core_agents.missing_tier",
                    f"registry[{idx}].tier is required and must be one of {sorted(TIER_VALUES)}",
                    agent_id=agent_id_str,
                )
            )
            continue
        if tier not in TIER_VALUES:
            errors.append(
                RegistryIssue(
                    "core_agents.invalid_tier",
                    f"registry[{idx}].tier must be one of {sorted(TIER_VALUES)}",
                    agent_id=agent_id_str,
                )
            )
            continue

        if tier != "core":
            continue

        core_count += 1

        availability = str(entry.get("availability") or "").strip()
        if availability != "runnable":
            errors.append(
                RegistryIssue(
                    "core_agents.not_runnable",
                    "tier=core requires availability=runnable",
                    agent_id=agent_id_str,
                )
            )
            continue

        action_trace_level = _normalize_action_trace_level(entry.get("action_trace_level"))
        if action_trace_level not in {"L0", "L1", "L2"}:
            errors.append(
                RegistryIssue(
                    "core_agents.invalid_action_trace_level",
                    "tier=core requires action_trace_level in {L0,L1,L2}",
                    agent_id=agent_id_str,
                )
            )

        adapter_raw = entry.get("adapter")
        if not _nonempty_str(adapter_raw):
            errors.append(
                RegistryIssue(
                    "core_agents.missing_adapter",
                    "tier=core runnable entries must set adapter path",
                    agent_id=agent_id_str,
                )
            )
            continue

        adapter_path = _resolve_path(root, str(adapter_raw).strip())
        if not adapter_path.exists():
            errors.append(
                RegistryIssue(
                    "core_agents.adapter_not_found",
                    f"adapter not found: {adapter_path}",
                    agent_id=agent_id_str,
                )
            )
            continue
        if not adapter_path.is_file():
            errors.append(
                RegistryIssue(
                    "core_agents.adapter_not_file",
                    f"adapter path is not a file: {adapter_path}",
                    agent_id=agent_id_str,
                )
            )
            continue

        manifest_path = adapter_path.parent / "adapter_manifest.json"
        if not manifest_path.exists():
            errors.append(
                RegistryIssue(
                    "core_agents.missing_manifest",
                    f"missing adapter_manifest.json at {manifest_path}",
                    agent_id=agent_id_str,
                )
            )

    if core_count == 0:
        warnings.append(
            RegistryIssue(
                "core_agents.empty",
                "no tier=core agents found (expected a representative runnable core set)",
            )
        )

    return RegistryValidationReport(errors=errors, warnings=warnings)
