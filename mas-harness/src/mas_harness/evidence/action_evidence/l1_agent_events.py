from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mas_harness.evidence.action_evidence.agent_events_v1 import (
    AgentEventsV1ParseError,
    load_agent_events_v1_jsonl,
)
from mas_harness.evidence.action_evidence.base import (
    ActionEvidenceCollectionError,
    EventStreamSpec,
    RawAgentEvent,
    resolve_path_on_host,
)


@dataclass(frozen=True)
class L1AgentEventsCollector:
    event_stream: EventStreamSpec
    repo_root: Path | None = None
    run_dir: Path | None = None

    def collect(self) -> list[RawAgentEvent]:
        fmt = self.event_stream.format.strip().lower()
        if fmt != "agent_events_v1":
            raise ActionEvidenceCollectionError(
                "unsupported L1 event_stream.format (expected 'agent_events_v1'): "
                f"{self.event_stream.format!r}"
            )

        path = resolve_path_on_host(
            self.event_stream.path_on_host, repo_root=self.repo_root, run_dir=self.run_dir
        )
        if not path.exists():
            raise ActionEvidenceCollectionError(f"agent_events file not found: {path}")

        try:
            return load_agent_events_v1_jsonl(path)
        except AgentEventsV1ParseError as e:
            raise ActionEvidenceCollectionError(str(e)) from e
