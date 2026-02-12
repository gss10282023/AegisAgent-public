from __future__ import annotations

import json
import sys
from pathlib import Path

from mas_harness.phases.phase0_artifacts import (
    Phase0Config,
    ensure_phase0_artifacts,
    finalize_run_manifest_action_evidence,
)


def _run_cli(argv: list[str]) -> int:
    from mas_harness.cli import run_agent

    old_argv = sys.argv
    try:
        sys.argv = argv
        return int(run_agent.main())
    finally:
        sys.argv = old_argv


def test_finalize_run_manifest_sets_level_from_device_input_trace(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    run_dir = tmp_path / "run_with_l2_trace"
    ensure_phase0_artifacts(
        out_dir=run_dir,
        repo_root=repo_root,
        cfg=Phase0Config(
            execution_mode="planner_only",
            env_profile="mas_core",
            agent_name="unit_test",
            availability="runnable",
            action_trace_level="none",
            action_trace_source="none",
        ),
        seed=0,
    )

    evidence_dir = run_dir / "episode_0000" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "device_input_trace.jsonl").write_text(
        json.dumps(
            {
                "step_idx": 0,
                "source_level": "L2",
                "event_type": "noop",
                "payload": {},
                "timestamp_ms": 0,
                "mapping_warnings": [],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    finalize_run_manifest_action_evidence(run_dir=run_dir)
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["action_trace_level"] == "L2"
    assert manifest["action_trace_source"] == "comm_proxy"
    assert "action_trace_degraded_from" not in manifest
    assert "action_trace_degraded_reason" not in manifest


def test_finalize_run_manifest_degrades_on_invalid_trace(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    run_dir = tmp_path / "run_with_invalid_trace"
    ensure_phase0_artifacts(
        out_dir=run_dir,
        repo_root=repo_root,
        cfg=Phase0Config(
            execution_mode="planner_only",
            env_profile="mas_core",
            agent_name="unit_test",
            availability="runnable",
            action_trace_level="L1",
            action_trace_source="agent_events",
        ),
        seed=0,
    )

    evidence_dir = run_dir / "episode_0000" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    trace_path = evidence_dir / "device_input_trace.jsonl"
    trace_path.write_text("{not json\n", encoding="utf-8")

    finalize_run_manifest_action_evidence(run_dir=run_dir)
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["action_trace_level"] == "none"
    assert manifest["action_trace_source"] == "none"
    assert manifest["action_trace_degraded_from"] == "L1"
    assert manifest["action_trace_degraded_reason"] == "invalid_device_input_trace"

    quarantined = evidence_dir / "device_input_trace.invalid.jsonl"
    assert quarantined.exists()
    assert trace_path.exists()
    assert trace_path.read_text(encoding="utf-8").strip() == ""


def test_finalize_run_manifest_degrades_on_empty_trace(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    run_dir = tmp_path / "run_with_empty_trace"
    ensure_phase0_artifacts(
        out_dir=run_dir,
        repo_root=repo_root,
        cfg=Phase0Config(
            execution_mode="planner_only",
            env_profile="mas_core",
            agent_name="unit_test",
            availability="runnable",
            action_trace_level="L0",
            action_trace_source="mas_executor",
        ),
        seed=0,
    )

    evidence_dir = run_dir / "episode_0000" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "device_input_trace.jsonl").write_text("", encoding="utf-8")

    finalize_run_manifest_action_evidence(run_dir=run_dir)
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["action_trace_level"] == "none"
    assert manifest["action_trace_source"] == "none"
    assert manifest["action_trace_degraded_from"] == "L0"
    assert manifest["action_trace_degraded_reason"] == "empty_device_input_trace"


def test_run_agent_strict_action_evidence_fails_when_finalize_degrades(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    case_dir = repo_root / "mas-public" / "cases" / "smoke_001"

    adapter_dir = tmp_path / "bad_adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)

    adapter_path = adapter_dir / "adapter.py"
    adapter_path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pathlib import Path",
                "from typing import Any, Dict",
                "",
                "from mas_harness.evidence import EvidenceWriter, _TINY_PNG_1X1, stable_sha256",
                "",
                "",
                "class Adapter:",
                "    def run_case(",
                "        self, *, case_dir: Path, evidence_dir: Path, ctx",
                "    ) -> Dict[str, Any]:",
                "        writer = EvidenceWriter(",
                "            run_dir=ctx.output_dir,",
                "            case_id='strict_action_evidence_case',",
                "            seed=ctx.seed,",
                "            run_mode='phase3',",
                "            metadata={",
                "                **(ctx.run_metadata or {}),",
                "                'agent_id': str(ctx.registry_entry.get('agent_id') or ''),",
                "            },",
                "            episode_dir=evidence_dir,",
                "        )",
                "        obs = {",
                "            'screenshot_png': _TINY_PNG_1X1,",
                "            'screen_info': {",
                "                'width_px': 1080,",
                "                'height_px': 1920,",
                "                'density_dpi': 440,",
                "                'surface_orientation': 0,",
                "            },",
                "            'a11y_tree': {",
                "                'nodes': [",
                "                    {'id': 'root', 'role': 'window', 'children': ['label']},",
                "                    {",
                "                        'id': 'label',",
                "                        'role': 'text',",
                "                        'text': 'x',",
                "                        'bounds': [0, 0, 200, 50],",
                "                    },",
                "                ]",
                "            },",
                "            'ui_hash': stable_sha256('x'.encode('utf-8')),",
                "            'foreground_package': 'com.example',",
                "            'foreground_activity': 'com.example.Main',",
                "        }",
                "        writer.record_observation(step=0, observation=obs)",
                "        writer.record_agent_call({",
                "            'step_idx': 0,",
                "            'agent_name': 'bad_adapter',",
                "            'provider': None,",
                "            'model_id': None,",
                "            'base_url': None,",
                "            'input_digest': stable_sha256({'step': 0}),",
                "            'response_digest': stable_sha256({'type': 'wait'}),",
                "            'latency_ms': 0,",
                "            'tokens_in': None,",
                "            'tokens_out': None,",
                "            'error': None,",
                "        })",
                "        writer.record_agent_action(step=0, action={'type': 'wait'})",
                "        writer.record_action(",
                "            step=0, action={'type': 'wait'}, result={'ok': True}",
                "        )",
                "        writer.write_summary({",
                "            'status': 'inconclusive',",
                "            'steps': 1,",
                "            'terminated_reason': 'adapter_done',",
                "            'failure_class': None,",
                "            'task_success': {",
                "                'score': 0.0,",
                "                'success': False,",
                "                'conclusive': False,",
                "                'reason': 'unit_test',",
                "                'oracle_id': None,",
                "                'oracle_type': None,",
                "            },",
                "            'violations': [],",
                "            'notes': {'runner': 'bad_adapter'},",
                "        })",
                "        writer.close()",
                "        (Path(evidence_dir) / 'device_input_trace.jsonl').write_text(",
                "            '{bad json\\n', encoding='utf-8'",
                "        )",
                "        return {}",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (adapter_dir / "adapter_manifest.json").write_text(
        json.dumps(
            {
                "agent_id": "bad_adapter_l1",
                "default_coord_space": "physical_px",
                "action_evidence": {
                    "level": "L1",
                    "source": "agent_events",
                    "event_stream": {
                        "format": "agent_events_v1",
                        "path_on_host": "does_not_exist.jsonl",
                    },
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    registry_path = tmp_path / "agent_registry.yaml"
    registry_path.write_text(
        "\n".join(
            [
                "- agent_id: bad_adapter_l1",
                "  availability: runnable",
                "  env_profile: android_world_compat",
                "  action_trace_level: L1",
                f"  adapter: {adapter_path}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "strict_action_evidence_run"
    rc = _run_cli(
        [
            "run_agent",
            "--agent_id",
            "bad_adapter_l1",
            "--case_dir",
            str(case_dir),
            "--output",
            str(out_dir),
            "--registry",
            str(registry_path),
            "--strict_action_evidence",
        ]
    )
    assert rc == 2

    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["action_trace_level"] == "none"
    assert manifest["action_trace_source"] == "none"
    assert manifest["action_trace_degraded_from"] == "L1"
    assert manifest["action_trace_degraded_reason"] == "invalid_device_input_trace"

    summary = json.loads((out_dir / "episode_0000" / "summary.json").read_text(encoding="utf-8"))
    assert summary["failure_class"] == "infra_failed"
    assert summary["terminated_reason"] == "strict_action_evidence"
    assert summary["action_trace_level"] == "none"
    assert summary["action_trace_source"] == "none"
