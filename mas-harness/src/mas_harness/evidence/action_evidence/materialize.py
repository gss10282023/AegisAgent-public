from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from mas_harness.evidence.action_evidence.base import (
    ActionEvidenceCollectionError,
    ActionEvidenceSpec,
    ActionEvidenceSpecError,
    EventStreamSpec,
    parse_action_evidence_spec,
    resolve_path_on_host,
)
from mas_harness.evidence.action_evidence.device_input_trace_writer import DeviceInputTraceWriter
from mas_harness.evidence.action_evidence.l1_agent_events import L1AgentEventsCollector
from mas_harness.evidence.action_evidence.l1_mapping import materialize_l1_device_input_trace


class ActionEvidenceMaterializationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionEvidenceMaterializationResult:
    level: str
    source: str
    materialized: bool
    mapping_stats: dict[str, Any] | None = None


def _normalize_level(level: str | None) -> str:
    return str(level or "").strip().upper()


def _normalize_source(source: str | None) -> str:
    return str(source or "").strip().lower()


def materialize_action_evidence(
    *,
    adapter_manifest: Mapping[str, Any] | None,
    repo_root: Path,
    run_dir: Path,
    evidence_dir: Path,
    dry_run_ingest_events: Path | None = None,
) -> ActionEvidenceMaterializationResult:
    """Materialize device_input_trace.jsonl for one episode based on adapter_manifest.

    Phase3 3b-7c unified post-processing entry:
      - L0: executor already wrote device_input_trace.jsonl (no-op here)
      - L1: collector + mapper -> device_input_trace.jsonl
      - L2: recorder + mapper -> device_input_trace.jsonl (mapper reads comm_proxy_trace.jsonl)

    This function only performs the materialization step; validation/finalize is
    handled by the runner via `finalize_run_manifest_action_evidence`.
    """

    spec: ActionEvidenceSpec | None
    if adapter_manifest is None:
        spec = None
    else:
        try:
            spec = parse_action_evidence_spec(adapter_manifest)
        except ActionEvidenceSpecError as e:
            raise ActionEvidenceMaterializationError(str(e)) from e

    if spec is None:
        if dry_run_ingest_events is not None:
            raise ActionEvidenceMaterializationError(
                "dry_run_ingest_events requires adapter_manifest.action_evidence.level='L1'"
            )
        return ActionEvidenceMaterializationResult(
            level="L0",
            source="mas_executor",
            materialized=False,
            mapping_stats=None,
        )

    level = _normalize_level(spec.level)
    source = _normalize_source(spec.source)

    if level == "L1":
        if source != "agent_events":
            raise ActionEvidenceMaterializationError(
                "unsupported L1 action_evidence.source (expected 'agent_events'): "
                f"{spec.source!r}"
            )

        event_stream = spec.event_stream
        if dry_run_ingest_events is not None:
            event_stream = EventStreamSpec(
                format=spec.event_stream.format,
                path_on_host=str(dry_run_ingest_events),
            )
        else:
            candidate = resolve_path_on_host(
                spec.event_stream.path_on_host,
                repo_root=repo_root,
                run_dir=run_dir,
            )
            # Phase3 3b-4d: only materialize when path_on_host exists.
            if not candidate.exists():
                return ActionEvidenceMaterializationResult(
                    level="L1",
                    source="agent_events",
                    materialized=False,
                    mapping_stats=None,
                )

        try:
            collector = L1AgentEventsCollector(
                event_stream=event_stream,
                repo_root=repo_root,
                run_dir=run_dir,
            )
            raw_events = collector.collect()
        except ActionEvidenceCollectionError as e:
            raise ActionEvidenceMaterializationError(str(e)) from e

        trace_path = Path(evidence_dir) / "device_input_trace.jsonl"
        with DeviceInputTraceWriter(trace_path, mode="w") as writer:
            materialize_l1_device_input_trace(raw_events, writer=writer)

        return ActionEvidenceMaterializationResult(
            level="L1",
            source="agent_events",
            materialized=True,
            mapping_stats={"mapped_event_count": len(raw_events)},
        )

    if level == "L2":
        if source != "comm_proxy":
            raise ActionEvidenceMaterializationError(
                "unsupported L2 action_evidence.source (expected 'comm_proxy'): " f"{spec.source!r}"
            )

        trace_path = Path(evidence_dir) / "comm_proxy_trace.jsonl"
        if not trace_path.exists():
            return ActionEvidenceMaterializationResult(
                level="L2",
                source="comm_proxy",
                materialized=False,
                mapping_stats=None,
            )

        from mas_harness.evidence.action_evidence.comm_proxy_trace import (
            load_comm_proxy_trace_jsonl,
        )
        from mas_harness.evidence.action_evidence.l2_mapping import (
            materialize_l2_device_input_trace,
        )

        comm_events: Sequence[Mapping[str, Any]] = load_comm_proxy_trace_jsonl(trace_path)
        device_trace = Path(evidence_dir) / "device_input_trace.jsonl"
        with DeviceInputTraceWriter(device_trace, mode="w") as writer:
            stats = materialize_l2_device_input_trace(comm_events, writer=writer)

        return ActionEvidenceMaterializationResult(
            level="L2",
            source="comm_proxy",
            materialized=True,
            mapping_stats=dict(stats) if isinstance(stats, dict) else None,
        )

    raise ActionEvidenceMaterializationError(
        f"unsupported action_evidence.level (expected L1/L2): {spec.level!r}"
    )
