from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas_harness.reporting import build_aggregate_report


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def test_reporting_core_filter_and_vr_core(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"

    # Episode A: core trusted.
    ep_a = runs_dir / "run_0000" / "episode_0000"
    _write_json(
        ep_a / "summary.json",
        {
            "case_id": "c_core",
            "agent_id": "agent_core",
            "status": "success",
            "audit": {"is_core_trusted": True},
        },
    )
    assertions_a = [
        {
            "assertion_id": "SA_CoreHighriskFail",
            "result": "FAIL",
            "severity": "high",
            "risk_weight_bucket": None,
            "mapped_sp": "SP1",
            "mapped_primitive": "P1",
            "mapped_boundary": "B1",
            "impact_level": "highrisk",
            "evidence_refs": ["a.jsonl:L1"],
            "payload": {},
            "inconclusive_reason": None,
            "applicable": True,
        },
        {
            "assertion_id": "SA_CoreCanaryInconclusive",
            "result": "INCONCLUSIVE",
            "severity": "med",
            "risk_weight_bucket": None,
            "mapped_sp": "SP1",
            "mapped_primitive": "P2",
            "mapped_boundary": "B1",
            "impact_level": "canary",
            "evidence_refs": ["a.jsonl:L2"],
            "payload": {},
            "inconclusive_reason": "missing_evidence",
            "applicable": True,
        },
        # Excluded from VR_core due to impact_level.
        {
            "assertion_id": "SA_CoreLowPass",
            "result": "PASS",
            "severity": "low",
            "risk_weight_bucket": None,
            "mapped_sp": "SP2",
            "mapped_primitive": "P3",
            "mapped_boundary": "B2",
            "impact_level": "low",
            "evidence_refs": ["a.jsonl:L3"],
            "payload": {},
            "inconclusive_reason": None,
            "applicable": True,
        },
    ]
    _write_jsonl(ep_a / "assertions.jsonl", assertions_a)

    # Episode B: non-core trusted.
    ep_b = runs_dir / "run_0001" / "episode_0000"
    _write_json(
        ep_b / "summary.json",
        {
            "case_id": "c_noncore",
            "agent_id": "agent_noncore",
            "status": "success",
            "audit": {"is_core_trusted": False},
        },
    )
    assertions_b = [
        {
            "assertion_id": "SA_NonCoreCanaryInconclusive",
            "result": "INCONCLUSIVE",
            "severity": "med",
            "risk_weight_bucket": None,
            "mapped_sp": "SP3",
            "mapped_primitive": "P9",
            "mapped_boundary": "B9",
            "impact_level": "canary",
            "evidence_refs": ["b.jsonl:L1"],
            "payload": {},
            "inconclusive_reason": "missing_other_evidence",
            "applicable": True,
        },
    ]
    _write_jsonl(ep_b / "assertions.jsonl", assertions_b)

    report = build_aggregate_report(runs_dir=runs_dir)

    assert "metrics_all" in report
    assert "metrics_core" in report
    assert "vr_core" in report
    assert "top_inconclusive_reasons_overall" in report
    assert "top_inconclusive_reasons_core" in report
    assert "top_inconclusive_reasons_by_assertion_id" in report

    metrics_all = report["metrics_all"]
    metrics_core = report["metrics_core"]

    assert metrics_all["overall"]["total"] == len(assertions_a) + len(assertions_b)
    assert metrics_core["overall"]["total"] == len(assertions_a)

    # Core metrics should exclude the non-core-only assertion id.
    assert "SA_NonCoreCanaryInconclusive" in metrics_all["by_assertion_id"]
    assert "SA_NonCoreCanaryInconclusive" not in metrics_core["by_assertion_id"]

    vr = report["vr_core"]
    assert vr["applicable_total"] == 2
    assert vr["counts_applicable"]["FAIL"] == 1
    assert vr["counts_applicable"]["INCONCLUSIVE"] == 1
    assert vr["counts_applicable"]["PASS"] == 0
    assert vr["fail_rate"] == pytest.approx(0.5)
    assert vr["inconclusive_rate"] == pytest.approx(0.5)

    # Inconclusive reasons should be scoped correctly.
    overall_reasons = {
        row["reason"]: row["count"] for row in report["top_inconclusive_reasons_overall"]
    }
    core_reasons = {row["reason"]: row["count"] for row in report["top_inconclusive_reasons_core"]}
    assert overall_reasons == {"missing_evidence": 1, "missing_other_evidence": 1}
    assert core_reasons == {"missing_evidence": 1}

    by_id_core = report["top_inconclusive_reasons_by_assertion_id"]
    assert "SA_CoreCanaryInconclusive" in by_id_core
    assert by_id_core["SA_CoreCanaryInconclusive"][0]["reason"] == "missing_evidence"
    assert by_id_core["SA_CoreCanaryInconclusive"][0]["count"] == 1
