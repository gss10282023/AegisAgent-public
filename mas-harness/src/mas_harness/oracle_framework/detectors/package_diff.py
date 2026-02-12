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


def _canonical_package_name(raw: Any) -> Optional[str]:
    s = _nonempty_str(raw)
    if s is None:
        return None
    if s.startswith("package:"):
        s = s[len("package:") :].strip()
    if "=" in s:
        # Support `pm list packages -f` output: package:/path/base.apk=com.pkg
        s = s.split("=", 1)[-1].strip()
    return s or None


def _parse_packages_text(text: str) -> list[str]:
    pkgs: set[str] = set()
    for line in str(text or "").splitlines():
        name = _canonical_package_name(line)
        if name is None:
            continue
        pkgs.add(name)
    return sorted(pkgs)


def _parse_packages_json(obj: Any) -> list[str] | None:
    if isinstance(obj, list):
        pkgs: set[str] = set()
        for item in obj:
            name = _canonical_package_name(item)
            if name is None:
                continue
            pkgs.add(name)
        return sorted(pkgs)

    if isinstance(obj, Mapping):
        for key in ("packages", "package_list", "installed_packages", "installed"):
            raw = obj.get(key)
            parsed = _parse_packages_json(raw)
            if parsed is not None:
                return parsed
    return None


def _parse_packages_any(value: Any) -> list[str] | None:
    parsed = _parse_packages_json(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str):
        return _parse_packages_text(value)
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


def _parse_packages_from_artifacts(
    evidence_dir: Path, paths: list[str]
) -> tuple[list[str] | None, list[str]]:
    if not paths:
        return None, []

    def _rank(p: str) -> tuple[int, str]:
        name = Path(p).name.lower()
        has_pkg = 0 if ("package" in name or "packages" in name) else 1
        return has_pkg, name

    for rel in sorted(paths, key=_rank):
        full = evidence_dir / rel
        suffix = full.suffix.lower()
        if suffix in {".json"}:
            obj = _try_load_json(full)
            parsed = _parse_packages_any(obj)
            if parsed is not None:
                return parsed, [rel]
            continue
        text = _try_load_text(full)
        if text is None:
            continue
        parsed = _parse_packages_text(text)
        if parsed:
            return parsed, [rel]
    return None, []


@dataclass(frozen=True)
class _SnapshotCandidate:
    oracle_name: str
    phase: str
    line_no: int
    packages: list[str]
    evidence_refs: set[str]
    used_artifacts: bool

    @property
    def score(self) -> int:
        return (1_000_000 if self.used_artifacts else 0) + len(self.packages)


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

    # Fallback: best overall, even if different oracle_name.
    pre = _pick_best_candidate(pre_cands, phase="pre")
    post = _pick_best_candidate(post_cands, phase="post")
    return pre, post


class PackageDiffDetector(Detector):
    detector_id = "package_diff"
    evidence_required = ("oracle_trace.jsonl",)
    produces_fact_ids = ("fact.package_diff",)

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
            packages, used_artifacts = _parse_packages_from_artifacts(evidence_dir, artifact_list)
            if packages is not None:
                for p in used_artifacts:
                    refs.add(f"artifact:{p}")
                cand = _SnapshotCandidate(
                    oracle_name=oracle_name,
                    phase=phase,
                    line_no=int(line_no),
                    packages=list(packages),
                    evidence_refs=refs,
                    used_artifacts=True,
                )
            else:
                packages = _parse_packages_any(obj.get("result_preview"))
                if packages is None:
                    continue
                cand = _SnapshotCandidate(
                    oracle_name=oracle_name,
                    phase=phase,
                    line_no=int(line_no),
                    packages=list(packages),
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

        pre_set = set(pre_snap.packages)
        post_set = set(post_snap.packages)
        new_packages = sorted(post_set - pre_set)
        removed_packages = sorted(pre_set - post_set)

        return [
            Fact(
                fact_id="fact.package_diff",
                oracle_source="device_query",
                evidence_refs=sorted(
                    pre_snap.evidence_refs | post_snap.evidence_refs | {path.name}
                ),
                payload={
                    "new_packages": new_packages,
                    "removed_packages": removed_packages,
                    "pre_count": len(pre_set),
                    "post_count": len(post_set),
                },
            )
        ]
