from __future__ import annotations

import json
from pathlib import Path

from mas_harness.oracles.zoo.base import ORACLE_EVIDENCE_SCHEMA_VERSION, assert_oracle_event_v0
from mas_harness.runtime.run_public import run_case


def test_oracle_evidence_min_fields(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    case_dir = repo_root / "mas-public" / "cases" / "smoke_001"

    summary = run_case(
        case_dir=case_dir,
        out_dir=tmp_path,
        seed=0,
        schemas_dir=repo_root / "mas-spec" / "schemas",
    )

    case_id = summary["case_id"]
    run_dir = tmp_path / "public" / case_id / "seed_0"
    oracle_trace = run_dir / "oracle_trace.jsonl"

    lines = oracle_trace.read_text(encoding="utf-8").splitlines()
    assert lines, f"missing oracle evidence: {oracle_trace}"

    events = [json.loads(line) for line in lines]
    for ev in events:
        assert_oracle_event_v0(ev)
        assert ev["evidence_schema_version"] == ORACLE_EVIDENCE_SCHEMA_VERSION

    oracle_id = summary["task_success_details"]["oracle_id"]
    assert any(ev.get("phase") == "post" and ev.get("oracle_id") == oracle_id for ev in events)
