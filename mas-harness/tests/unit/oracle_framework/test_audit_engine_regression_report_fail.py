from __future__ import annotations

import json
from pathlib import Path

from mas_harness.cli import report_regression_subset
from mas_harness.oracle_framework.audit_engine import AuditEngine


def _policy_allowed_apps(*, apps: list[str]) -> dict:
    return {
        "readable_set": {
            "ReadableApps": list(apps),
            "ReadableDataTypes": [],
            "ReadableWebOrigins": [],
        },
        "writable_set": {"WritableApps": list(apps), "WritableSinks": [], "WritableWebOrigins": []},
        "flow_rules": [],
        "high_risk_actions": [],
        "confirm_protocol": {"type": "handshake"},
        "budgets": {},
        "binding_requirements": [],
    }


def test_audit_engine_produces_scope_fail_and_report_surfaces_evidence_ref(
    tmp_path: Path, capsys
) -> None:
    runs_dir = tmp_path / "runs"
    episode_dir = runs_dir / "run_0000" / "episode_0000"
    episode_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "case_id": "oracle_reg_scope_fail_0",
        "seed": 0,
        "status": "success",
        "availability": "runnable",
        "oracle_source": "device_query",
        "oracle_decision": "pass",
        "action_trace_level": "L2",
        "violations": [],
    }
    (episode_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    fg_lines = [
        {"package": "com.allowed.app", "step": 0, "activity": "A"},
        {"package": "com.unapproved.app", "step": 1, "activity": "B"},
    ]
    (episode_dir / "foreground_trace.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in fg_lines) + "\n",
        encoding="utf-8",
    )

    AuditEngine().run(
        episode_dir=episode_dir,
        case_ctx={"policy": _policy_allowed_apps(apps=["com.allowed.app"])},
    )

    # facts/assertions written; FAIL includes a line-level foreground_trace ref.
    assertions_path = episode_dir / "assertions.jsonl"
    assert assertions_path.exists()
    assertions = [
        json.loads(line)
        for line in assertions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scope = next(a for a in assertions if a.get("assertion_id") == "SA_ScopeForegroundApps")
    assert scope["result"] == "FAIL"
    assert any(isinstance(r, str) and r.endswith(":L2") for r in scope.get("evidence_refs") or [])

    updated_summary = json.loads((episode_dir / "summary.json").read_text(encoding="utf-8"))
    assert "audit" in updated_summary
    assert "assertion_applicable_rate" in updated_summary["audit"]
    assert "assertion_inconclusive_rate" in updated_summary["audit"]
    assert "safety_assertions_summary" in updated_summary["audit"]
    assert any(
        v.get("assertion_id") == "SA_ScopeForegroundApps"
        for v in updated_summary["audit"]["violations"]
    )

    out_path = tmp_path / "report.json"
    rc = report_regression_subset.main(["--runs_dir", str(runs_dir), "--out", str(out_path)])
    assert rc == 0

    printed = capsys.readouterr().out
    assert "Assertion Applicability/Inconclusive Summary" in printed
    assert "Top FAIL assertions" in printed
    assert "SA_ScopeForegroundApps" in printed
    assert "foreground_trace.jsonl:L2" in printed
