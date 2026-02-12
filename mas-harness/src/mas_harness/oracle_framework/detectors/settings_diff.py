from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.detectors._jsonl import iter_jsonl_objects
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _canonical_namespace(value: Any) -> Optional[str]:
    ns = _nonempty_str(value)
    if ns is None:
        return None
    return ns.strip().lower() or None


def _canonical_key(value: Any) -> Optional[str]:
    k = _nonempty_str(value)
    if k is None:
        return None
    return k.strip() or None


def _canonical_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _parse_settings_snapshot_json(obj: Any) -> dict[tuple[str, str], str | None] | None:
    if isinstance(obj, list):
        out: dict[tuple[str, str], str | None] = {}
        for item in obj:
            if not isinstance(item, Mapping):
                continue
            ns = _canonical_namespace(item.get("namespace") or item.get("ns"))
            key = _canonical_key(item.get("key"))
            if ns is None or key is None:
                continue
            val = _canonical_value(item.get("value"))
            if "value" not in item:
                val = _canonical_value(item.get("val") if "val" in item else item.get("actual"))
            out[(ns, key)] = val
        return out

    if isinstance(obj, Mapping):
        for key in ("settings", "values", "snapshot"):
            if key in obj:
                parsed = _parse_settings_snapshot_json(obj.get(key))
                if parsed is not None:
                    return parsed

        # Nested map: {namespace:{key:value}}
        out_nested: dict[tuple[str, str], str | None] = {}
        for ns_raw, kv in obj.items():
            ns = _canonical_namespace(ns_raw)
            if ns is None:
                continue
            if not isinstance(kv, Mapping):
                continue
            for key_raw, val_raw in kv.items():
                k = _canonical_key(key_raw)
                if k is None:
                    continue
                out_nested[(ns, k)] = _canonical_value(val_raw)
        if out_nested:
            return out_nested

        # Flat map: {"secure:location_mode":"3"}
        out_flat: dict[tuple[str, str], str | None] = {}
        for k_raw, v in obj.items():
            k_str = _nonempty_str(k_raw)
            if k_str is None:
                continue
            if ":" not in k_str:
                continue
            ns_part, key_part = k_str.split(":", 1)
            ns = _canonical_namespace(ns_part)
            key = _canonical_key(key_part)
            if ns is None or key is None:
                continue
            out_flat[(ns, key)] = _canonical_value(v)
        if out_flat:
            return out_flat

    return None


def _parse_settings_snapshot_any(value: Any) -> dict[tuple[str, str], str | None] | None:
    parsed = _parse_settings_snapshot_json(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str):
        # Best-effort text: "namespace key value" per line.
        out: dict[tuple[str, str], str | None] = {}
        for line in value.splitlines():
            parts = [p for p in line.strip().split() if p]
            if len(parts) < 2:
                continue
            ns = _canonical_namespace(parts[0])
            key = _canonical_key(parts[1])
            if ns is None or key is None:
                continue
            val = _canonical_value(" ".join(parts[2:]) if len(parts) > 2 else None)
            out[(ns, key)] = val
        return out
    return None


def _artifact_paths(ev: Mapping[str, Any]) -> list[str]:
    artifacts = ev.get("artifacts")
    out: list[str] = []
    if not isinstance(artifacts, list):
        return out
    for a in artifacts:
        if not isinstance(a, Mapping):
            continue
        p = _nonempty_str(a.get("path"))
        if p is not None:
            out.append(p)
    return sorted(set(out))


def _try_load_text(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _try_load_json(path: Path) -> Any | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_snapshot_from_artifacts(
    evidence_dir: Path, paths: list[str]
) -> tuple[dict[tuple[str, str], str | None] | None, list[str]]:
    if not paths:
        return None, []

    def _rank(p: str) -> tuple[int, str]:
        name = Path(p).name.lower()
        has_settings = 0 if "setting" in name else 1
        return has_settings, name

    for rel in sorted(paths, key=_rank):
        full = evidence_dir / rel
        suffix = full.suffix.lower()
        if suffix in {".json"}:
            obj = _try_load_json(full)
            parsed = _parse_settings_snapshot_any(obj)
            if parsed is not None:
                return parsed, [rel]
            continue
        text = _try_load_text(full)
        if text is None:
            continue
        parsed = _parse_settings_snapshot_any(text)
        if parsed is not None:
            return parsed, [rel]

    return None, []


@dataclass(frozen=True)
class _SnapshotCandidate:
    oracle_name: str
    phase: str
    line_no: int
    values: dict[tuple[str, str], str | None]
    evidence_refs: set[str]
    used_artifacts: bool

    @property
    def score(self) -> int:
        return (1_000_000 if self.used_artifacts else 0) + len(self.values)


def _pick_best_candidate(cands: list[_SnapshotCandidate], *, phase: str) -> _SnapshotCandidate:
    if phase == "pre":
        return max(cands, key=lambda c: (c.score, -c.line_no, c.oracle_name))
    return max(cands, key=lambda c: (c.score, c.line_no, c.oracle_name))


def _select_snapshot_pair(
    pre_cands: list[_SnapshotCandidate],
    post_cands: list[_SnapshotCandidate],
) -> tuple[_SnapshotCandidate, _SnapshotCandidate] | None:
    if not pre_cands or not post_cands:
        return None

    by_oracle_pre: dict[str, list[_SnapshotCandidate]] = {}
    by_oracle_post: dict[str, list[_SnapshotCandidate]] = {}
    for c in pre_cands:
        by_oracle_pre.setdefault(c.oracle_name, []).append(c)
    for c in post_cands:
        by_oracle_post.setdefault(c.oracle_name, []).append(c)

    pairs: list[tuple[int, str, _SnapshotCandidate, _SnapshotCandidate]] = []
    for oracle_name in sorted(set(by_oracle_pre) & set(by_oracle_post)):
        pre = _pick_best_candidate(by_oracle_pre[oracle_name], phase="pre")
        post = _pick_best_candidate(by_oracle_post[oracle_name], phase="post")
        pairs.append((pre.score + post.score, oracle_name, pre, post))

    if pairs:
        _score, _oracle_name, pre, post = max(pairs, key=lambda x: (x[0], x[1]))
        return pre, post

    pre = _pick_best_candidate(pre_cands, phase="pre")
    post = _pick_best_candidate(post_cands, phase="post")
    return pre, post


class SettingsDiffDetector(Detector):
    detector_id = "settings_diff"
    evidence_required = ("oracle_trace.jsonl",)
    produces_fact_ids = ("fact.settings_diff",)

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        _ = case_ctx
        evidence_dir: Path = Path(getattr(pack, "evidence_dir", pack))
        path = evidence_dir / "oracle_trace.jsonl"
        if not path.exists():
            return []

        pre: list[_SnapshotCandidate] = []
        post: list[_SnapshotCandidate] = []

        for line_no, obj in iter_jsonl_objects(path):
            phase = _nonempty_str(obj.get("phase"))
            if phase not in {"pre", "post"}:
                continue

            oracle_name = _nonempty_str(obj.get("oracle_name")) or "unknown"
            refs: set[str] = {f"{path.name}:L{int(line_no)}"}

            artifact_list = _artifact_paths(obj)
            values, used_artifacts = _parse_snapshot_from_artifacts(evidence_dir, artifact_list)
            if values is not None:
                for p in used_artifacts:
                    refs.add(f"artifact:{p}")
                cand = _SnapshotCandidate(
                    oracle_name=oracle_name,
                    phase=phase,
                    line_no=int(line_no),
                    values=dict(values),
                    evidence_refs=refs,
                    used_artifacts=True,
                )
            else:
                values = _parse_settings_snapshot_any(obj.get("result_preview"))
                if values is None:
                    continue
                cand = _SnapshotCandidate(
                    oracle_name=oracle_name,
                    phase=phase,
                    line_no=int(line_no),
                    values=dict(values),
                    evidence_refs=refs,
                    used_artifacts=False,
                )

            if phase == "pre":
                pre.append(cand)
            else:
                post.append(cand)

        pair = _select_snapshot_pair(pre, post)
        if pair is None:
            return []
        pre_snap, post_snap = pair

        changed: list[dict[str, Any]] = []
        keys = set(pre_snap.values) | set(post_snap.values)
        for ns, key in sorted(keys, key=lambda x: (x[0], x[1])):
            before = pre_snap.values.get((ns, key))
            after = post_snap.values.get((ns, key))
            if before == after:
                continue
            changed.append({"namespace": ns, "key": key, "before": before, "after": after})

        return [
            Fact(
                fact_id="fact.settings_diff",
                oracle_source="device_query",
                evidence_refs=sorted(
                    pre_snap.evidence_refs | post_snap.evidence_refs | {path.name}
                ),
                payload={"changed": changed},
            )
        ]
