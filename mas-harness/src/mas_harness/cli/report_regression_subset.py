from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mas_harness.evidence.evidence_pack import ASSERTIONS_JSONL, resolve_episode_evidence_dir
from mas_harness.oracle_framework.schema_validators import assert_assertion_result_v0
from mas_harness.reporting import (
    build_aggregate_report,
    find_summary_paths,
    is_hard_oracle_benign_regression_summary,
    load_json_object,
    write_hard_oracle_benign_regression_action_level_report,
)


def _iter_assertion_results_for_summary(summary_path: Path) -> Iterable[Mapping[str, Any]]:
    evidence_dir = resolve_episode_evidence_dir(summary_path.parent)
    path = evidence_dir / ASSERTIONS_JSONL
    if not path.exists():
        return

    for _i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        try:
            assert_assertion_result_v0(obj)
        except Exception:
            continue
        yield obj


def _print_assertion_audit_sections(
    *,
    summary_paths: Sequence[Path],
    summaries: Sequence[Mapping[str, Any]],
) -> None:
    selected: list[tuple[Path, Mapping[str, Any]]] = [
        (p, s)
        for p, s in zip(summary_paths, summaries)
        if is_hard_oracle_benign_regression_summary(s)
    ]

    total = 0
    applicable_true = 0
    inconclusive = 0
    by_id: dict[str, dict[str, Any]] = {}

    for summary_path, _summary in selected:
        for r in _iter_assertion_results_for_summary(summary_path):
            assertion_id = str(r.get("assertion_id", "")).strip() or "unknown"
            result = str(r.get("result", "")).strip().upper()
            total += 1
            if r.get("applicable") is True:
                applicable_true += 1
            if result == "INCONCLUSIVE":
                inconclusive += 1

            row = by_id.setdefault(
                assertion_id,
                {
                    "PASS": 0,
                    "FAIL": 0,
                    "INCONCLUSIVE": 0,
                    "total": 0,
                    "applicable_true": 0,
                    "sample_fail_ref": None,
                },
            )
            if result in {"PASS", "FAIL", "INCONCLUSIVE"}:
                row[result] += 1
            row["total"] += 1
            if r.get("applicable") is True:
                row["applicable_true"] += 1
            if result == "FAIL" and row.get("sample_fail_ref") is None:
                refs = r.get("evidence_refs")
                if isinstance(refs, list):
                    for ref in refs:
                        if isinstance(ref, str) and ":L" in ref:
                            row["sample_fail_ref"] = ref
                            break
                    if row.get("sample_fail_ref") is None and refs:
                        row["sample_fail_ref"] = str(refs[0])

    def _rate(n: int, d: int) -> float:
        return float(n) / float(d) if d else 0.0

    print("\nAssertion Applicability/Inconclusive Summary")
    print(f"- selected_episodes: {len(selected)}")
    print(f"- total_assertions: {total}")
    print(f"- applicable_true: {applicable_true} (rate={_rate(applicable_true, total):.3f})")
    print(f"- inconclusive: {inconclusive} (rate={_rate(inconclusive, total):.3f})")

    if by_id:
        first_n = 10
        print(f"- by_assertion_id (first {first_n}):")
        for assertion_id in sorted(by_id.keys())[:first_n]:
            row = by_id[assertion_id]
            print(
                f"  - {assertion_id}: PASS={row['PASS']} FAIL={row['FAIL']} "
                f"INCONCLUSIVE={row['INCONCLUSIVE']} "
                f"applicable={row['applicable_true']}/{row['total']}"
            )

    print("\nTop FAIL assertions")
    failed_ids = sorted([aid for aid, row in by_id.items() if int(row.get("FAIL", 0)) > 0])
    if not failed_ids:
        print("- (none)")
        return
    for assertion_id in failed_ids:
        row = by_id[assertion_id]
        ref = row.get("sample_fail_ref")
        ref_suffix = f" sample_ref={ref}" if isinstance(ref, str) and ref else ""
        print(f"- {assertion_id}: FAIL={row['FAIL']}{ref_suffix}")


def _fmt_rate(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except Exception:
        return "0.000"


def _print_phase4_core_all_metrics(*, runs_dir: Path) -> None:
    report = build_aggregate_report(runs_dir=Path(runs_dir))

    core = report.get("metrics_core") if isinstance(report.get("metrics_core"), dict) else {}
    all_metrics = report.get("metrics_all") if isinstance(report.get("metrics_all"), dict) else {}
    vr_core = report.get("vr_core") if isinstance(report.get("vr_core"), dict) else {}

    def _print_metrics_block(label: str, metrics: Mapping[str, Any]) -> None:
        overall = metrics.get("overall") if isinstance(metrics.get("overall"), dict) else {}
        total = int(overall.get("total", 0)) if isinstance(overall.get("total"), int) else 0
        applicable_true = (
            int(overall.get("applicable_true", 0))
            if isinstance(overall.get("applicable_true"), int)
            else 0
        )
        print(f"\n{label}")
        print(f"- episodes_with_assertions: {int(metrics.get('episodes_with_assertions', 0) or 0)}")
        print(
            f"- applicable_true: {applicable_true}/{total} "
            f"(rate={_fmt_rate(overall.get('applicable_rate'))})"
        )
        print(
            f"- fail_rate={_fmt_rate(overall.get('fail_rate'))} "
            f"inconclusive_rate={_fmt_rate(overall.get('inconclusive_rate'))} "
            f"pass_rate={_fmt_rate(overall.get('pass_rate'))}"
        )

    print("\nPhase4 Assertion Metrics (core vs all)")
    _print_metrics_block(
        "Core metrics (core = tcb_captured + device_query)",
        core,
    )
    _print_metrics_block("All metrics (all episodes with assertions.jsonl)", all_metrics)

    if vr_core:
        counts = (
            vr_core.get("counts_applicable")
            if isinstance(vr_core.get("counts_applicable"), dict)
            else {}
        )
        denom = int(vr_core.get("applicable_total", 0) or 0)
        print("\nVR_core (core episodes; safety; canary/highrisk; denom=applicable_true)")
        print(f"- applicable_total: {denom}")
        print(
            f"- fail_rate={_fmt_rate(vr_core.get('fail_rate'))} "
            f"inconclusive_rate={_fmt_rate(vr_core.get('inconclusive_rate'))} "
            f"pass_rate={_fmt_rate(vr_core.get('pass_rate'))}"
        )
        if isinstance(counts.get("FAIL"), int) or isinstance(counts.get("INCONCLUSIVE"), int):
            print(
                f"- counts_applicable: FAIL={int(counts.get('FAIL', 0) or 0)} "
                f"INCONCLUSIVE={int(counts.get('INCONCLUSIVE', 0) or 0)}"
            )

    def _print_top_reasons(label: str, items: Any) -> None:
        print(f"\n{label}")
        if not isinstance(items, list) or not items:
            print("- (none)")
            return
        for row in items[:5]:
            reason = row.get("reason") if isinstance(row, dict) else None
            count = row.get("count") if isinstance(row, dict) else None
            print(f"- {reason}: {count}")

    _print_top_reasons(
        "Top INCONCLUSIVE reasons (all)",
        report.get("top_inconclusive_reasons_overall"),
    )
    _print_top_reasons(
        "Top INCONCLUSIVE reasons (core)",
        report.get("top_inconclusive_reasons_core"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase3 3b-8c: bucket hard-oracle benign regression subset by "
            "action_trace_level (L0/L1/L2; assumes no L3)."
        )
    )
    parser.add_argument(
        "--runs_dir", type=Path, required=True, help="Directory to scan for summary.json."
    )
    parser.add_argument("--out", type=Path, required=True, help="Output JSON report path.")
    parser.add_argument(
        "--min_unique_cases",
        type=int,
        default=0,
        help="Optional minimum required unique regression case_ids (default: 0).",
    )
    args = parser.parse_args(argv)

    write_hard_oracle_benign_regression_action_level_report(
        runs_dir=args.runs_dir,
        out_path=args.out,
        min_unique_cases=int(args.min_unique_cases),
    )
    print(f"Wrote regression action-level report to {args.out}")

    _print_phase4_core_all_metrics(runs_dir=args.runs_dir)

    summary_paths = find_summary_paths(args.runs_dir)
    summaries = [load_json_object(p) for p in summary_paths]
    _print_assertion_audit_sections(summary_paths=summary_paths, summaries=summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
