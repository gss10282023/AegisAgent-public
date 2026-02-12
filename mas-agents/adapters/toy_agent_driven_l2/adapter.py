from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict
from urllib.request import Request, urlopen

import yaml

from mas_harness.evidence import EvidenceWriter, stable_sha256
from mas_harness.examples import ToyEnv


def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(req, timeout=2.0) as resp:
        body = resp.read()
        return int(resp.getcode()), json.loads(body.decode("utf-8"))


def _get_json(url: str) -> tuple[int, Any]:
    with urlopen(url, timeout=2.0) as resp:
        body = resp.read()
        return int(resp.getcode()), json.loads(body.decode("utf-8"))


class ToyAgentDrivenL2Adapter:
    def run_case(self, *, case_dir: Path, evidence_dir: Path, ctx) -> Dict[str, Any]:
        case_id = case_dir.name
        task_path = case_dir / "task.yaml"
        if task_path.exists():
            try:
                task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
            except Exception:
                task = None
            if (
                isinstance(task, dict)
                and isinstance(task.get("task_id"), str)
                and task["task_id"].strip()
            ):
                case_id = task["task_id"].strip()

        base_url = str(os.environ.get("MAS_COMM_PROXY_BASE_URL") or "").strip()
        if not base_url:
            raise RuntimeError("MAS_COMM_PROXY_BASE_URL is required for toy_agent_driven_l2")
        act_path = str(os.environ.get("MAS_COMM_PROXY_ACT_PATH") or "/act").strip() or "/act"

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

        action: Dict[str, Any] = {
            "type": "tap",
            "x": 120,
            "y": 340,
            "coord_space": "physical_px",
            "note": "toy_agent_driven_l2",
        }

        input_digest = stable_sha256({"step": 0, "ui_hash": obs.get("ui_hash")})
        response_digest = stable_sha256(action)

        writer.record_agent_call(
            {
                "step_idx": 0,
                "agent_name": "ToyAgentDrivenL2Adapter",
                "provider": None,
                "model_id": None,
                "base_url": base_url,
                "input_digest": input_digest,
                "response_digest": response_digest,
                "latency_ms": 0,
                "tokens_in": None,
                "tokens_out": None,
                "error": None,
            }
        )
        normalized = writer.record_agent_action(step=0, action=action)

        comm_action = {"type": "tap", "x": 120, "y": 340, "coord_space": "physical_px"}
        _get_json(base_url + "/health")
        status, resp = _post_json(base_url + act_path, comm_action)
        ok = status == 200 and isinstance(resp, dict) and resp.get("ok") is True
        writer.record_action(
            step=0,
            action=normalized,
            result={"ok": ok, "resp": resp},
        )

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
                "runner": "toy_agent_driven_l2",
                "comm_proxy_base_url": base_url,
            },
        }
        summary = writer.write_summary(summary)
        writer.close()
        return summary


def create_adapter() -> ToyAgentDrivenL2Adapter:
    return ToyAgentDrivenL2Adapter()
