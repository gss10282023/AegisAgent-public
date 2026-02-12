from __future__ import annotations

import json
from pathlib import Path

from mas_harness.runtime.run_public import run_case


def _load_jsonl_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        obj = json.loads(raw)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def test_phase4_determinism_same_seed_smoke_case(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    case_dir = repo_root / "mas-public" / "cases" / "smoke_001"
    schemas_dir = repo_root / "mas-spec" / "schemas"

    out_dir_1 = tmp_path / "out1"
    out_dir_2 = tmp_path / "out2"

    seed = 0
    summary1 = run_case(
        case_dir=case_dir,
        out_dir=out_dir_1,
        seed=seed,
        schemas_dir=schemas_dir,
        repo_root=repo_root,
    )
    summary2 = run_case(
        case_dir=case_dir,
        out_dir=out_dir_2,
        seed=seed,
        schemas_dir=schemas_dir,
        repo_root=repo_root,
    )

    case_id = str(summary1.get("case_id") or "smoke_001")
    assert str(summary2.get("case_id") or "") == case_id

    episode_dir_1 = out_dir_1 / "public" / case_id / f"seed_{seed}"
    episode_dir_2 = out_dir_2 / "public" / case_id / f"seed_{seed}"

    facts_1 = _load_jsonl_rows(episode_dir_1 / "facts.jsonl")
    facts_2 = _load_jsonl_rows(episode_dir_2 / "facts.jsonl")
    facts_map_1 = {str(f.get("fact_id")): str(f.get("digest")) for f in facts_1}
    facts_map_2 = {str(f.get("fact_id")): str(f.get("digest")) for f in facts_2}
    assert facts_map_1 == facts_map_2

    assertions_1 = _load_jsonl_rows(episode_dir_1 / "assertions.jsonl")
    assertions_2 = _load_jsonl_rows(episode_dir_2 / "assertions.jsonl")
    assertions_map_1 = {
        str(a.get("assertion_id")): (
            str(a.get("result")),
            a.get("applicable"),
            a.get("inconclusive_reason"),
        )
        for a in assertions_1
    }
    assertions_map_2 = {
        str(a.get("assertion_id")): (
            str(a.get("result")),
            a.get("applicable"),
            a.get("inconclusive_reason"),
        )
        for a in assertions_2
    }
    assert assertions_map_1 == assertions_map_2
