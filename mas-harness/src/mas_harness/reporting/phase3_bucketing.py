from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PHASE3_ACTION_TRACE_LEVEL_BUCKETS: tuple[str, ...] = ("L0", "L1", "L2")
PHASE3_ACTION_EVIDENCE_BUCKETS: tuple[str, ...] = ("L0", "L1", "L2", "none")


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _episode_dir_for_summary(path: Path) -> Path | None:
    """Return the episode dir for a summary.json path, or None if not an episode summary."""

    if path.name != "summary.json":
        return None

    parent = path.parent
    if parent.name.startswith("episode_"):
        return parent
    if parent.name == "evidence" and parent.parent.name.startswith("episode_"):
        return parent.parent
    return None


def find_summary_paths(runs_dir: Path) -> list[Path]:
    """Return one summary.json path per episode under `runs_dir`.

    Runner outputs include both `episode_*/summary.json` and `episode_*/evidence/summary.json`.
    Reporting should avoid double-counting by preferring the episode-level summary when present.
    """

    episode_to_paths: dict[Path, list[Path]] = {}
    for p in sorted(Path(runs_dir).glob("**/summary.json")):
        episode_dir = _episode_dir_for_summary(p)
        if episode_dir is None:
            continue
        episode_to_paths.setdefault(episode_dir, []).append(p)

    out: list[Path] = []
    for episode_dir, paths in sorted(episode_to_paths.items(), key=lambda kv: str(kv[0])):
        preferred = episode_dir / "summary.json"
        if preferred in paths:
            out.append(preferred)
        else:
            out.append(sorted(paths)[0])
    return out


def load_json_object(path: Path) -> dict[str, Any]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"expected JSON object in {path}")
    return obj


def normalize_action_trace_level(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.lower() == "none":
        return "none"
    return s.upper()


def normalize_availability(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s:
        return None
    return s


def normalize_unavailable_reason(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    return s


def assert_no_l3_action_trace_level(
    levels: Iterable[str | None], *, where: str = "reporting"
) -> None:
    for level in levels:
        if level is None:
            continue
        if str(level).strip().upper() == "L3":
            raise ValueError(
                f"action_trace_level=L3 is not supported (Phase3 assumes no L3): {where}"
            )


def is_hard_oracle_benign_regression_summary(summary: Mapping[str, Any]) -> bool:
    case_id = summary.get("case_id")
    if not isinstance(case_id, str) or not case_id.startswith("oracle_reg_"):
        return False

    oracle_source = summary.get("oracle_source")
    if str(oracle_source or "").strip().lower() != "device_query":
        return False

    availability = summary.get("availability")
    if str(availability or "").strip().lower() != "runnable":
        return False

    return True


def bucket_hard_oracle_benign_regression_by_action_level(
    summaries: Sequence[Mapping[str, Any]],
    *,
    min_unique_cases: int = 0,
) -> dict[str, Any]:
    """Bucket hard-oracle benign regression summaries by action_trace_level.

    Phase3 3b-8c requires buckets for L0/L1/L2 and explicitly assumes no L3.
    """

    normalized_levels = [
        normalize_action_trace_level(s.get("action_trace_level")) for s in summaries
    ]
    assert_no_l3_action_trace_level(normalized_levels, where="summary.json inputs")

    selected = [s for s in summaries if is_hard_oracle_benign_regression_summary(s)]
    unique_cases = sorted(
        {
            c.strip()
            for c in (s.get("case_id") for s in selected)
            if isinstance(c, str) and c.strip()
        }
    )
    if min_unique_cases and len(unique_cases) < int(min_unique_cases):
        raise ValueError(
            f"expected ≥{int(min_unique_cases)} hard-oracle benign regression case(s), "
            f"got {len(unique_cases)}"
        )

    buckets: dict[str, dict[str, Any]] = {
        lvl: {"total_episodes": 0, "unique_agents": [], "unique_cases": [], "episodes": []}
        for lvl in PHASE3_ACTION_TRACE_LEVEL_BUCKETS
    }

    for s in selected:
        lvl = normalize_action_trace_level(s.get("action_trace_level"))
        if lvl not in PHASE3_ACTION_TRACE_LEVEL_BUCKETS:
            raise ValueError(
                "unexpected action_trace_level in regression reporting: "
                f"{lvl!r} (expected one of {list(PHASE3_ACTION_TRACE_LEVEL_BUCKETS)})"
            )
        agent_id = s.get("agent_id")
        case_id = s.get("case_id")
        buckets[lvl]["episodes"].append(
            {
                "agent_id": str(agent_id).strip()
                if isinstance(agent_id, str) and agent_id.strip()
                else None,
                "case_id": str(case_id).strip()
                if isinstance(case_id, str) and case_id.strip()
                else None,
                "status": s.get("status"),
                "oracle_decision": s.get("oracle_decision"),
            }
        )

    for lvl in PHASE3_ACTION_TRACE_LEVEL_BUCKETS:
        episodes = buckets[lvl]["episodes"]
        buckets[lvl]["total_episodes"] = len(episodes)
        buckets[lvl]["unique_agents"] = sorted(
            {e["agent_id"] for e in episodes if isinstance(e, dict) and e.get("agent_id")}
        )
        buckets[lvl]["unique_cases"] = sorted(
            {e["case_id"] for e in episodes if isinstance(e, dict) and e.get("case_id")}
        )

    return {
        "subset": {
            "type": "hard_oracle_benign_regression",
            "min_unique_cases": int(min_unique_cases),
            "total_selected_episodes": len(selected),
            "unique_case_ids": unique_cases,
        },
        "buckets": buckets,
    }


def bucket_action_evidence_by_action_level(
    summaries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bucket summaries by action_trace_level into Phase3 action-evidence buckets.

    Buckets are: L0/L1/L2/none. "none" includes missing/empty/non-string values.
    Phase3 explicitly assumes L3 never appears; raise fast if it does.
    """

    normalized_levels = [
        normalize_action_trace_level(s.get("action_trace_level")) for s in summaries
    ]
    assert_no_l3_action_trace_level(normalized_levels, where="summary.json inputs")

    buckets: dict[str, dict[str, Any]] = {
        lvl: {"total_episodes": 0, "unique_agents": [], "unique_cases": [], "episodes": []}
        for lvl in PHASE3_ACTION_EVIDENCE_BUCKETS
    }

    for s, raw_lvl in zip(summaries, normalized_levels):
        lvl = raw_lvl or "none"
        if lvl not in PHASE3_ACTION_EVIDENCE_BUCKETS:
            raise ValueError(
                "unexpected action_trace_level in reporting: "
                f"{lvl!r} (expected one of {list(PHASE3_ACTION_EVIDENCE_BUCKETS)})"
            )

        agent_id = s.get("agent_id")
        case_id = s.get("case_id")
        buckets[lvl]["episodes"].append(
            {
                "agent_id": str(agent_id).strip()
                if isinstance(agent_id, str) and agent_id.strip()
                else None,
                "case_id": str(case_id).strip()
                if isinstance(case_id, str) and case_id.strip()
                else None,
                "availability": s.get("availability"),
                "status": s.get("status"),
                "oracle_decision": s.get("oracle_decision"),
            }
        )

    for lvl in PHASE3_ACTION_EVIDENCE_BUCKETS:
        episodes = buckets[lvl]["episodes"]
        buckets[lvl]["total_episodes"] = len(episodes)
        buckets[lvl]["unique_agents"] = sorted(
            {e["agent_id"] for e in episodes if isinstance(e, dict) and e.get("agent_id")}
        )
        buckets[lvl]["unique_cases"] = sorted(
            {e["case_id"] for e in episodes if isinstance(e, dict) and e.get("case_id")}
        )

    return {"total_episodes": len(summaries), "buckets": buckets}


def bucket_unavailable_reasons(
    registry_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bucket agent_registry entries by unavailable_reason (Phase3 3c-3b)."""

    buckets: dict[str, dict[str, Any]] = {}
    total_unavailable = 0

    for entry in registry_entries:
        availability = normalize_availability(entry.get("availability"))
        if availability != "unavailable":
            continue

        total_unavailable += 1
        reason = (
            normalize_unavailable_reason(entry.get("unavailable_reason"))
            or "missing_unavailable_reason"
        )
        agent_id = entry.get("agent_id")
        agent_id_str = agent_id.strip() if isinstance(agent_id, str) and agent_id.strip() else None

        bucket = buckets.setdefault(reason, {"total_agents": 0, "agent_ids": []})
        if agent_id_str:
            bucket["agent_ids"].append(agent_id_str)

    for bucket in buckets.values():
        agent_ids = sorted(
            {a for a in bucket.get("agent_ids", []) if isinstance(a, str) and a.strip()}
        )
        bucket["agent_ids"] = agent_ids
        bucket["total_agents"] = len(agent_ids)

    return {
        "total_registry_entries": len(registry_entries),
        "total_unavailable_entries": total_unavailable,
        "buckets": buckets,
    }


def write_action_evidence_distribution_report(
    *,
    runs_dir: Path,
    out_path: Path,
) -> dict[str, Any]:
    summary_paths = find_summary_paths(runs_dir)
    summaries = [load_json_object(p) for p in summary_paths]
    distribution = bucket_action_evidence_by_action_level(summaries)

    payload = {
        "runs_dir": str(Path(runs_dir)),
        "summary_paths": [str(p) for p in summary_paths],
        "action_evidence_distribution": distribution,
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(_json_dumps_canonical(payload) + "\n", encoding="utf-8")
    tmp_path.replace(out_path)
    return payload


def write_hard_oracle_benign_regression_action_level_report(
    *,
    runs_dir: Path,
    out_path: Path,
    min_unique_cases: int = 0,
) -> dict[str, Any]:
    summary_paths = find_summary_paths(runs_dir)
    summaries = [load_json_object(p) for p in summary_paths]
    report = bucket_hard_oracle_benign_regression_by_action_level(
        summaries, min_unique_cases=min_unique_cases
    )
    payload = {
        "runs_dir": str(Path(runs_dir)),
        "summary_paths": [str(p) for p in summary_paths],
        **report,
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(_json_dumps_canonical(payload) + "\n", encoding="utf-8")
    tmp_path.replace(out_path)
    return payload


def write_unavailable_reasons_distribution_report(
    *,
    registry_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    from mas_harness.integration.agents.registry import load_agent_registry

    registry_entries = load_agent_registry(Path(registry_path))
    distribution = bucket_unavailable_reasons(registry_entries)
    payload = {
        "registry_path": str(Path(registry_path)),
        "unavailable_reasons_distribution": distribution,
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(_json_dumps_canonical(payload) + "\n", encoding="utf-8")
    tmp_path.replace(out_path)
    return payload
