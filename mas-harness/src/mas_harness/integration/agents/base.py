from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from mas_harness.phases.phase0_artifacts import Phase0Config


class AgentAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdapterRunContext:
    repo_root: Path
    schemas_dir: Path
    seed: int
    phase0_cfg: Phase0Config
    run_metadata: Dict[str, Any]
    registry_entry: Dict[str, Any]
    output_dir: Path


@runtime_checkable
class AgentAdapter(Protocol):
    def run_case(
        self,
        *,
        case_dir: Path,
        evidence_dir: Path,
        ctx: AdapterRunContext,
    ) -> Dict[str, Any]: ...


def require_str_field(entry: Dict[str, Any], key: str, *, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AgentAdapterError(f"missing required string field: {where}.{key}")
    return value.strip()


def optional_str_field(entry: Dict[str, Any], key: str) -> Optional[str]:
    value = entry.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
