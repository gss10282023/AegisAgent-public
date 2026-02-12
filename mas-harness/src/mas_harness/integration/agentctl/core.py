from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from mas_harness.integration.agents.base import AgentAdapterError
from mas_harness.integration.agents.registry import load_agent_registry
from mas_harness.phases.phase0_artifacts import Phase0Config, ensure_phase0_artifacts
from mas_harness.phases.phase3_smoke_cases import (
    build_fixed_smoke_case_open_settings,
    build_nl_smoke_case,
    write_case_dir,
)
from mas_harness.runtime import runner
from mas_harness.spec.validate_case import load_and_validate_case


def _utc_timestamp() -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y%m%d_%H%M%S")


def default_agentctl_output_dir(*, repo_root: Path, agent_id: str) -> Path:
    return (repo_root / "runs" / "agentctl" / agent_id / _utc_timestamp()).resolve()


@dataclass(frozen=True)
class RunnableAgent:
    agent_id: str
    availability: str
    env_profile: str
    execution_mode: str
    action_trace_level: str
    adapter_path: Path
    adapter_exists: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "availability": self.availability,
            "env_profile": self.env_profile,
            "execution_mode": self.execution_mode,
            "action_trace_level": self.action_trace_level,
            "adapter_path": str(self.adapter_path),
            "adapter_exists": bool(self.adapter_exists),
        }


def list_runnable_agents(
    *,
    registry_path: Path,
    repo_root: Path,
    include_missing_adapter: bool = False,
) -> list[RunnableAgent]:
    entries = load_agent_registry(registry_path)
    agents: list[RunnableAgent] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("availability") or "").strip() != "runnable":
            continue

        agent_id = str(entry.get("agent_id") or "").strip()
        if not agent_id:
            continue

        adapter_raw = entry.get("adapter")
        if not isinstance(adapter_raw, str) or not adapter_raw.strip():
            continue
        adapter_path = runner._resolve_path(repo_root, adapter_raw.strip())
        adapter_exists = adapter_path.exists()
        if not include_missing_adapter and not adapter_exists:
            continue

        env_profile = str(entry.get("env_profile") or "").strip() or "mas_core"
        action_trace_level = str(entry.get("action_trace_level") or "").strip() or "none"

        exec_supported = entry.get("execution_mode_supported")
        execution_mode = "planner_only"
        if isinstance(exec_supported, list) and exec_supported:
            first = exec_supported[0]
            if isinstance(first, str) and first.strip():
                execution_mode = first.strip()

        agents.append(
            RunnableAgent(
                agent_id=agent_id,
                availability="runnable",
                env_profile=env_profile,
                execution_mode=execution_mode,
                action_trace_level=action_trace_level,
                adapter_path=adapter_path,
                adapter_exists=adapter_exists,
            )
        )

    agents.sort(key=lambda a: a.agent_id)
    return agents


def build_agentctl_case_dir(
    *,
    output_dir: Path,
    mode: str,
    goal: str | None,
    max_steps: int,
    schemas_dir: Path,
) -> Path:
    case_dir = Path(output_dir) / "_agentctl_case"
    case_dir.mkdir(parents=True, exist_ok=True)

    if mode == "fixed":
        specs = build_fixed_smoke_case_open_settings(max_steps=int(max_steps))
    elif mode == "nl":
        if goal is None:
            raise SystemExit("--goal is required for nl mode")
        specs = build_nl_smoke_case(goal=goal, max_steps=int(max_steps))
    else:
        raise SystemExit(f"unknown mode: {mode}")

    write_case_dir(case_dir=case_dir, specs=specs)
    load_and_validate_case(case_dir=case_dir, schemas_dir=schemas_dir)
    return case_dir


def run_agentctl_case(
    *,
    agent_id: str,
    mode: str,
    goal: Optional[str],
    max_steps: int,
    output_dir: Path,
    registry_path: Path,
    schemas_dir: Path,
    execution_mode: str,
    env_profile_override: Optional[str],
    repo_root: Path,
    device_serial: Optional[str],
    adb_path: str,
    snapshot_tag: Optional[str],
    reset_strategy: Optional[str],
    seed: int,
) -> None:
    entries = load_agent_registry(registry_path)
    registry_entry = runner._find_registry_entry(entries, agent_id)
    if registry_entry is None:
        raise SystemExit(f"agent_id not found in registry: {agent_id} ({registry_path})")

    availability = str(registry_entry.get("availability") or "").strip()
    if availability != "runnable":
        raise SystemExit(
            f"agentctl only supports runnable agents (got: {availability!r} for {agent_id})"
        )

    adapter_raw = registry_entry.get("adapter")
    if not isinstance(adapter_raw, str) or not adapter_raw.strip():
        raise SystemExit(f"runnable agent missing adapter path in registry: {agent_id}")
    adapter_path = runner._resolve_path(repo_root, adapter_raw.strip())
    if not adapter_path.exists():
        raise SystemExit(f"adapter not found for agent_id={agent_id}: {adapter_path}")

    resolved_env_profile = (
        env_profile_override or str(registry_entry.get("env_profile") or "").strip() or "mas_core"
    )
    registry_action_trace_level = (
        str(registry_entry.get("action_trace_level") or "").strip() or None
    )

    if mode == "fixed":
        run_purpose = "agentctl_fixed"
        oracle_source = "device_query"
    elif mode == "nl":
        run_purpose = "agentctl_nl"
        oracle_source = "none"
    else:
        raise SystemExit(f"unknown mode: {mode}")

    case_dir = build_agentctl_case_dir(
        output_dir=output_dir,
        mode=mode,
        goal=goal,
        max_steps=int(max_steps),
        schemas_dir=schemas_dir,
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    phase0_cfg = Phase0Config(
        execution_mode=execution_mode,
        env_profile=resolved_env_profile,
        agent_name=agent_id,
        agent_provider=os.environ.get("MAS_AGENT_PROVIDER"),
        agent_model_id=os.environ.get("MAS_AGENT_MODEL_ID"),
        agent_base_url=os.environ.get("MAS_AGENT_BASE_URL"),
        reset_strategy=reset_strategy,
        snapshot_tag=snapshot_tag,
        android_serial=device_serial,
        adb_path=adb_path,
        availability=availability,
        action_trace_level=registry_action_trace_level,
        guard_enforcement="unenforced",
        evidence_trust_level="tcb_captured",
        oracle_source=oracle_source,
        run_purpose=run_purpose,
    )

    run_metadata = ensure_phase0_artifacts(
        out_dir=output_dir,
        repo_root=repo_root,
        cfg=phase0_cfg,
        seed=int(seed),
    )

    try:
        runner._run_runnable(
            agent_id=agent_id,
            registry_entry=registry_entry,
            case_dir=case_dir,
            output=output_dir,
            seed=int(seed),
            repo_root=repo_root,
            schemas_dir=schemas_dir,
            phase0_cfg=phase0_cfg,
            run_metadata=run_metadata,
            dry_run_ingest_events=None,
            comm_proxy_mode="off",
            strict_action_evidence=False,
        )
    except AgentAdapterError:
        raise
    except Exception as e:
        raise AgentAdapterError(f"agentctl {mode} failed: {e!r}")
