from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from mas_harness.evidence import EvidenceWriter, stable_sha256
from mas_harness.examples import ToyEnv


class ToyAgentDrivenL1Adapter:
    def run_case(self, *, case_dir: Path, evidence_dir: Path, ctx) -> Dict[str, Any]:
        case_id = case_dir.name
        task_path = case_dir / "task.yaml"
        if task_path.exists():
            try:
                task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
            except Exception:
                task = None
            if isinstance(task, dict) and isinstance(task.get("task_id"), str) and task["task_id"].strip():
                case_id = task["task_id"].strip()

        # Emit a deterministic raw agent event stream at the path declared by
        # adapter_manifest.json so run_agent can materialize L1 device_input_trace.jsonl.
        raw_events_path = ctx.output_dir / "agent_events.jsonl"
        if not raw_events_path.exists():
            fixture = (
                ctx.repo_root
                / "mas-harness"
                / "tests"
                / "fixtures"
                / "agent_events_l1_sample.jsonl"
            )
            if fixture.exists():
                raw_events_path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

        writer = EvidenceWriter(
            run_dir=ctx.output_dir,
            case_id=case_id,
            seed=ctx.seed,
            run_mode="phase3",
            metadata={
                **(ctx.run_metadata or {}),
                "agent_id": str(ctx.registry_entry.get("agent_id") or ""),
            },
            episode_dir=evidence_dir,
        )

        env = ToyEnv()
        env.reset()
        obs = env.observe()
        writer.record_observation(step=0, observation=obs)

        action: Dict[str, Any] = {"type": "wait", "duration_ms": 1, "note": "toy_agent_driven_l1"}
        input_digest = stable_sha256({"step": 0, "ui_hash": obs.get("ui_hash")})
        response_digest = stable_sha256(action)

        writer.record_agent_call(
            {
                "step_idx": 0,
                "agent_name": "ToyAgentDrivenL1Adapter",
                "provider": None,
                "model_id": None,
                "base_url": None,
                "input_digest": input_digest,
                "response_digest": response_digest,
                "latency_ms": 0,
                "tokens_in": None,
                "tokens_out": None,
                "error": None,
            }
        )
        normalized = writer.record_agent_action(step=0, action=action)
        writer.record_action(step=0, action=normalized, result={"ok": True, "dry_run": True})

        summary = {
            "status": "inconclusive",
            "steps": 1,
            "terminated_reason": "dry_run",
            "failure_class": None,
            "task_success_details": {
                "score": 0.0,
                "success": False,
                "conclusive": False,
                "reason": "dry_run",
                "oracle_id": None,
                "oracle_type": None,
            },
            "violations": [],
            "notes": {
                "runner": "toy_agent_driven_l1",
                "raw_events_path": str(raw_events_path),
            },
        }
        summary = writer.write_summary(summary)
        writer.close()
        return summary


def create_adapter() -> ToyAgentDrivenL1Adapter:
    return ToyAgentDrivenL1Adapter()
