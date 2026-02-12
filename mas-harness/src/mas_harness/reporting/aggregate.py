from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from mas_harness.evidence.evidence_pack import ASSERTIONS_JSONL, resolve_episode_evidence_dir
from mas_harness.oracle_framework.schema_validators import assert_assertion_result_v0
from mas_harness.reporting.phase3_bucketing import find_summary_paths, load_json_object


def _normalize_agent_id(summary: Mapping[str, Any]) -> str:
    raw = summary.get("agent_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "unknown"


def _normalize_sp(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _normalize_assertion_id(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _normalize_inconclusive_reason(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "missing_inconclusive_reason"


def _is_core_trusted_summary(summary: Mapping[str, Any]) -> bool:
    audit = summary.get("audit")
    if isinstance(audit, Mapping) and audit.get("is_core_trusted") is True:
        return True
    return False


@dataclass(frozen=True)
class _EpisodeAssertions:
    agent_id: str
    is_core_trusted: bool
    results: list[dict[str, Any]]


def _new_counts() -> dict[str, int]:
    return {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0}


def _new_bucket() -> dict[str, Any]:
    return {
        "total": 0,
        "applicable_true": 0,
        "counts_total": _new_counts(),
        "counts_applicable": _new_counts(),
    }


def _bucket_add(bucket: dict[str, Any], *, result: Any, applicable: bool) -> None:
    bucket["total"] = int(bucket.get("total", 0)) + 1
    if applicable:
        bucket["applicable_true"] = int(bucket.get("applicable_true", 0)) + 1

    res = str(result or "").strip().upper()
    if res in {"PASS", "FAIL", "INCONCLUSIVE"}:
        counts_total = bucket.get("counts_total") or {}
        counts_total[res] = int(counts_total.get(res, 0)) + 1
        bucket["counts_total"] = counts_total

        if applicable:
            counts_applicable = bucket.get("counts_applicable") or {}
            counts_applicable[res] = int(counts_applicable.get(res, 0)) + 1
            bucket["counts_applicable"] = counts_applicable


def _rate(n: int, d: int) -> float:
    return float(n) / float(d) if d else 0.0


def _bucket_to_stats(bucket: Mapping[str, Any]) -> dict[str, Any]:
    total = int(bucket.get("total", 0))
    applicable_true = int(bucket.get("applicable_true", 0))
    counts_total = dict(bucket.get("counts_total") or _new_counts())
    counts_applicable = dict(bucket.get("counts_applicable") or _new_counts())

    passed_app = int(counts_applicable.get("PASS", 0))
    failed_app = int(counts_applicable.get("FAIL", 0))
    inconc_app = int(counts_applicable.get("INCONCLUSIVE", 0))

    inconc_total = int(counts_total.get("INCONCLUSIVE", 0))

    return {
        "total": total,
        "applicable_true": applicable_true,
        "applicable_rate": _rate(applicable_true, total),
        "PASS": int(counts_total.get("PASS", 0)),
        "FAIL": int(counts_total.get("FAIL", 0)),
        "INCONCLUSIVE": inconc_total,
        "counts_applicable": {
            "PASS": passed_app,
            "FAIL": failed_app,
            "INCONCLUSIVE": inconc_app,
        },
        "pass_rate": _rate(passed_app, applicable_true),
        "fail_rate": _rate(failed_app, applicable_true),
        "inconclusive_rate": _rate(inconc_app, applicable_true),
        "inconclusive_rate_total": _rate(inconc_total, total),
    }


def _counter_top_list(counter: Counter[str], *, top_n: int) -> list[dict[str, Any]]:
    items = sorted(counter.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    return [
        {"reason": reason, "count": int(count)} for reason, count in items[: max(0, int(top_n))]
    ]


def _compute_inconclusive_reason_stats(
    episodes: Sequence[_EpisodeAssertions],
    *,
    top_n: int = 20,
    top_n_by_assertion_id: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    overall: Counter[str] = Counter()
    by_assertion: dict[str, Counter[str]] = defaultdict(Counter)

    for ep in episodes:
        for r in ep.results:
            if str(r.get("result") or "").strip().upper() != "INCONCLUSIVE":
                continue
            if r.get("applicable") is not True:
                continue
            aid = _normalize_assertion_id(r.get("assertion_id"))
            reason = _normalize_inconclusive_reason(r.get("inconclusive_reason"))
            overall[reason] += 1
            by_assertion[aid][reason] += 1

    by_assertion_out: dict[str, list[dict[str, Any]]] = {}
    for aid in sorted(by_assertion.keys()):
        by_assertion_out[aid] = _counter_top_list(
            by_assertion[aid], top_n=int(top_n_by_assertion_id)
        )
    return _counter_top_list(overall, top_n=int(top_n)), by_assertion_out


def _compute_metrics_for_episodes(episodes: Sequence[_EpisodeAssertions]) -> dict[str, Any]:
    by_assertion_id: dict[str, dict[str, Any]] = defaultdict(_new_bucket)
    by_sp: dict[str, dict[str, Any]] = defaultdict(_new_bucket)
    by_agent: dict[str, dict[str, Any]] = defaultdict(_new_bucket)
    by_agent_sp: dict[tuple[str, str], dict[str, Any]] = defaultdict(_new_bucket)

    total_bucket = _new_bucket()

    for ep in episodes:
        agent_id = ep.agent_id
        for r in ep.results:
            aid = _normalize_assertion_id(r.get("assertion_id"))
            sp = _normalize_sp(r.get("mapped_sp"))
            applicable = r.get("applicable") is True
            res = r.get("result")

            _bucket_add(total_bucket, result=res, applicable=applicable)
            _bucket_add(by_assertion_id[aid], result=res, applicable=applicable)
            _bucket_add(by_sp[sp], result=res, applicable=applicable)
            _bucket_add(by_agent[agent_id], result=res, applicable=applicable)
            _bucket_add(by_agent_sp[(agent_id, sp)], result=res, applicable=applicable)

    by_assertion_id_out: dict[str, Any] = {}
    for aid in sorted(by_assertion_id.keys()):
        by_assertion_id_out[aid] = _bucket_to_stats(by_assertion_id[aid])

    by_sp_out: dict[str, Any] = {}
    for sp in sorted(by_sp.keys()):
        by_sp_out[sp] = _bucket_to_stats(by_sp[sp])

    by_agent_out: dict[str, Any] = {}
    for agent_id in sorted(by_agent.keys()):
        by_agent_out[agent_id] = _bucket_to_stats(by_agent[agent_id])

    by_agent_sp_rows: list[dict[str, Any]] = []
    for (agent_id, sp), bucket in sorted(by_agent_sp.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        by_agent_sp_rows.append({"agent_id": agent_id, "mapped_sp": sp, **_bucket_to_stats(bucket)})

    def _top_assertions(*, key: str, top_n: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for aid, bucket in by_assertion_id.items():
            counts_app = bucket.get("counts_applicable") or {}
            total_app = int(bucket.get("applicable_true", 0))
            row = {
                "assertion_id": aid,
                "applicable_true": total_app,
                "fail_applicable": int(counts_app.get("FAIL", 0)),
                "inconclusive_applicable": int(counts_app.get("INCONCLUSIVE", 0)),
            }
            if int(row.get(key, 0)) <= 0:
                continue
            rows.append(row)

        rows.sort(key=lambda r: (-int(r.get(key, 0)), str(r.get("assertion_id") or "")))
        return rows[: max(0, int(top_n))]

    overall_stats = _bucket_to_stats(total_bucket)
    return {
        "episodes_with_assertions": int(len(episodes)),
        "overall": overall_stats,
        "by_assertion_id": by_assertion_id_out,
        "by_sp": by_sp_out,
        "by_agent": by_agent_out,
        "by_agent_sp": by_agent_sp_rows,
        "top_fail_assertions": _top_assertions(key="fail_applicable", top_n=10),
        "top_inconclusive_assertions": _top_assertions(key="inconclusive_applicable", top_n=10),
    }


def _compute_vr_core(episodes_core: Sequence[_EpisodeAssertions]) -> dict[str, Any]:
    applicable_total = 0
    passed = 0
    failed = 0
    inconclusive = 0

    for ep in episodes_core:
        for r in ep.results:
            aid = r.get("assertion_id")
            if not _is_safety_assertion(aid):
                continue
            impact = str(r.get("impact_level") or "").strip().lower()
            if impact not in {"canary", "highrisk"}:
                continue
            if r.get("applicable") is not True:
                continue
            applicable_total += 1
            res = str(r.get("result") or "").strip().upper()
            if res == "PASS":
                passed += 1
            elif res == "FAIL":
                failed += 1
            elif res == "INCONCLUSIVE":
                inconclusive += 1

    return {
        "definition": {
            "episode_subset": "metrics_core (audit.is_core_trusted == true)",
            "assertion_subset": "safety assertions only (assertion_id startswith 'SA_')",
            "impact_levels": ["canary", "highrisk"],
            "denominator": "applicable_true only",
        },
        "applicable_total": int(applicable_total),
        "counts_applicable": {
            "PASS": int(passed),
            "FAIL": int(failed),
            "INCONCLUSIVE": int(inconclusive),
        },
        "pass_rate": _rate(passed, applicable_total),
        "fail_rate": _rate(failed, applicable_total),
        "inconclusive_rate": _rate(inconclusive, applicable_total),
    }


def _iter_assertion_results_for_summary(
    summary_path: Path,
) -> list[dict[str, Any]]:
    evidence_dir = resolve_episode_evidence_dir(summary_path.parent)
    path = evidence_dir / ASSERTIONS_JSONL
    if not path.exists():
        return []

    out: list[dict[str, Any]] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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
        out.append(obj)
    return out


def _is_safety_assertion(assertion_id: Any) -> bool:
    return isinstance(assertion_id, str) and assertion_id.startswith("SA_")


def _accumulate_counts(
    *,
    results: Sequence[Mapping[str, Any]],
    counts: dict[str, int],
    counts_applicable: dict[str, int],
) -> None:
    for r in results:
        res = r.get("result")
        applicable = r.get("applicable") is True
        if isinstance(res, str) and res in {"PASS", "FAIL", "INCONCLUSIVE"}:
            counts[res] = int(counts.get(res, 0)) + 1
            if applicable:
                counts_applicable[res] = int(counts_applicable.get(res, 0)) + 1


def _rates_from_counts(
    *,
    total: int,
    applicable_total: int,
    counts_applicable: Mapping[str, int],
) -> dict[str, Any]:
    def rate(n: int, d: int) -> float:
        return float(n) / float(d) if d else 0.0

    passed = int(counts_applicable.get("PASS", 0))
    failed = int(counts_applicable.get("FAIL", 0))
    inconclusive = int(counts_applicable.get("INCONCLUSIVE", 0))
    return {
        "total": int(total),
        "applicable_total": int(applicable_total),
        "applicable_rate": rate(applicable_total, total),
        "pass_rate": rate(passed, applicable_total),
        "fail_rate": rate(failed, applicable_total),
        "inconclusive_rate": rate(inconclusive, applicable_total),
        "counts_applicable": {"PASS": passed, "FAIL": failed, "INCONCLUSIVE": inconclusive},
    }


def aggregate_summaries(
    summaries: Sequence[Mapping[str, Any]],
    *,
    runs_dir: Path,
) -> dict[str, Any]:
    total = len(summaries)
    success = sum(1 for s in summaries if s.get("status") == "success")
    return {
        "runs_dir": str(Path(runs_dir)),
        "total_cases": total,
        "success_cases": success,
        "success_rate": (success / total) if total else 0.0,
        "cases": list(summaries),
    }


def build_aggregate_report(*, runs_dir: Path) -> dict[str, Any]:
    summary_paths = find_summary_paths(Path(runs_dir))
    summaries = [load_json_object(p) for p in summary_paths]
    report = aggregate_summaries(summaries, runs_dir=Path(runs_dir))
    report["summary_paths"] = [str(p) for p in summary_paths]

    episodes: list[_EpisodeAssertions] = []
    for summary_path, summary in zip(summary_paths, summaries):
        results = _iter_assertion_results_for_summary(summary_path)
        if not results:
            continue
        episodes.append(
            _EpisodeAssertions(
                agent_id=_normalize_agent_id(summary),
                is_core_trusted=_is_core_trusted_summary(summary),
                results=results,
            )
        )

    episodes_core = [ep for ep in episodes if ep.is_core_trusted]

    report["metrics_all"] = _compute_metrics_for_episodes(episodes)
    report["metrics_core"] = _compute_metrics_for_episodes(episodes_core)
    report["vr_core"] = _compute_vr_core(episodes_core)

    top_overall, _by_id_overall = _compute_inconclusive_reason_stats(episodes)
    top_core, by_id_core = _compute_inconclusive_reason_stats(episodes_core)
    report["top_inconclusive_reasons_overall"] = top_overall
    report["top_inconclusive_reasons_core"] = top_core
    report["top_inconclusive_reasons_by_assertion_id"] = by_id_core

    # Backward-compatible summary (Phase4 reporting v0).
    # NOTE: This uses the all-episodes scope. New tooling should prefer metrics_all/metrics_core.
    total = 0
    applicable_total = 0
    counts_applicable = {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0}

    safety_total = 0
    safety_applicable_total = 0
    safety_counts_applicable = {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0}

    for ep in episodes:
        total += len(ep.results)
        applicable_total += sum(1 for r in ep.results if r.get("applicable") is True)
        _accumulate_counts(results=ep.results, counts={}, counts_applicable=counts_applicable)

        safety = [r for r in ep.results if _is_safety_assertion(r.get("assertion_id"))]
        safety_total += len(safety)
        safety_applicable_total += sum(1 for r in safety if r.get("applicable") is True)
        _accumulate_counts(results=safety, counts={}, counts_applicable=safety_counts_applicable)

    report["assertions"] = {
        "overall": _rates_from_counts(
            total=total,
            applicable_total=applicable_total,
            counts_applicable=counts_applicable,
        ),
        "safety": _rates_from_counts(
            total=safety_total,
            applicable_total=safety_applicable_total,
            counts_applicable=safety_counts_applicable,
        ),
    }
    return report


def write_aggregate_report(*, runs_dir: Path, out_path: Path) -> dict[str, Any]:
    payload = build_aggregate_report(runs_dir=Path(runs_dir))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(out_path)
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate MAS run summaries into a report.")
    parser.add_argument("--runs_dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    write_aggregate_report(runs_dir=args.runs_dir, out_path=args.out)
    print(f"Wrote report to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
