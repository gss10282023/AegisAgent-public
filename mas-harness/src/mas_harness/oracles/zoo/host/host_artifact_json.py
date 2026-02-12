"""Host-side JSON artifact oracles.

Phase 2 Step 12 (Oracle Zoo v1, E 类): Host-side Artifact Oracles（宿主机回执）.

Implements `HostArtifactJsonOracle`:
  - locate the newest JSON artifact under `ARTIFACTS_ROOT` (path or glob)
  - enforce an episode time window using host file mtime
  - optionally clear matching artifacts in `pre_check` (pollution control)
  - parse JSON and match expected key fields
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from mas_harness.oracles.zoo.base import (
    Oracle,
    OracleContext,
    OracleEvidence,
    make_decision,
    make_oracle_event,
    make_query,
    now_ms,
)
from mas_harness.oracles.zoo.registry import register_oracle
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256, stable_sha256
from mas_harness.oracles.zoo.utils.time_window import TimeWindow


def _artifacts_root() -> Optional[Path]:
    root = os.environ.get("ARTIFACTS_ROOT") or os.environ.get("MAS_ARTIFACTS_ROOT")
    if not root:
        return None
    return Path(root)


def _resolve_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    root = _artifacts_root()
    if root is None:
        return p
    return root / p


def _find_matching(glob_pattern: str) -> Sequence[Path]:
    root = _artifacts_root()
    if root is None:
        return []
    resolved_root: Path
    try:
        resolved_root = root.resolve()
    except Exception:  # pragma: no cover
        resolved_root = root

    matches: list[Path] = []
    for p in root.glob(glob_pattern):
        try:
            resolved_p = p.resolve()
        except Exception:
            resolved_p = p
        if resolved_p == resolved_root or not resolved_p.is_relative_to(resolved_root):
            continue
        if p.is_file():
            matches.append(p)
    return sorted(matches)


def _pick_latest_by_mtime(paths: Sequence[Path]) -> Optional[Path]:
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def _stat_mtime_ms(path: Path) -> int:
    return int(path.stat().st_mtime * 1000)


def _pick_latest_in_window(paths: Sequence[Path], window: TimeWindow) -> Optional[Path]:
    candidates = [p for p in paths if p.exists() and window.contains(_stat_mtime_ms(p))]
    if not candidates:
        return None
    return max(candidates, key=_stat_mtime_ms)


def _write_bytes_artifact(
    ctx: OracleContext, *, rel_path: Path, data: bytes, mime: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if ctx.episode_dir is None:
        return None, "missing episode_dir (cannot persist artifact)"

    try:
        out_path = ctx.episode_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        sha256 = stable_file_sha256(out_path)
        return (
            {
                "path": rel_path.as_posix(),
                "sha256": sha256,
                "bytes": len(data),
                "mime": mime,
            },
            None,
        )
    except Exception as e:  # pragma: no cover
        return None, f"artifact_write_failed:{type(e).__name__}:{e}"


def _get_by_path(obj: Any, path: str) -> Tuple[bool, Any]:
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
            continue
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(cur):
                cur = cur[idx]
                continue
        return False, None
    return True, cur


def _match_expected(obj: Any, expected: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    matched: Dict[str, Any] = {}
    mismatches: Dict[str, Any] = {}
    for key, exp in expected.items():
        found, got = _get_by_path(obj, str(key))
        if found and got == exp:
            matched[str(key)] = got
        else:
            mismatches[str(key)] = {"expected": exp, "got": got, "found": found}
    return matched, mismatches


class HostArtifactJsonOracle(Oracle):
    oracle_id = "host_artifact_json"
    oracle_name = "host_artifact_json"
    oracle_type = "hard"
    capabilities_required = ("host_artifacts_required",)

    def __init__(
        self,
        *,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        expected: Optional[Mapping[str, Any]] = None,
        clear_before_run: bool = False,
    ) -> None:
        if (not path) == (not glob):
            raise ValueError("HostArtifactJsonOracle requires exactly one of: path, glob")
        self._path = str(path) if path else None
        self._glob = str(glob) if glob else None
        self._expected = dict(expected) if expected else {}
        self._clear = bool(clear_before_run)

    def _missing_host_capability_event(self, *, phase: str, reason: str) -> Dict[str, Any]:
        reason = str(reason)
        if "host_artifacts_required" not in reason:
            reason = f"missing host_artifacts_required: {reason}"
        return make_oracle_event(
            ts_ms=now_ms(),
            oracle_id=self.oracle_id,
            oracle_name=self.oracle_name,
            oracle_type=self.oracle_type,
            phase=phase,
            queries=[
                make_query(
                    query_type="host_file",
                    path=str(self._path or self._glob or "ARTIFACTS_ROOT"),
                    timeout_ms=0,
                    serial=None,
                )
            ],
            result_for_digest={"missing": ["host_artifacts_required"], "reason": reason},
            anti_gaming_notes=[
                (
                    "Hard oracle: reads a host-side callback artifact; requires "
                    "ARTIFACTS_ROOT to be set."
                ),
            ],
            decision=make_decision(
                success=False,
                score=0.0,
                reason=reason,
                conclusive=False,
            ),
            capabilities_required=list(self.capabilities_required),
            missing_capabilities=["host_artifacts_required"],
        )

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        del ctx
        if not self._clear:
            return []

        removed: list[str] = []
        errors: list[str] = []
        targets: Sequence[Path]
        if self._path is not None:
            targets = [_resolve_path(self._path)]
        else:
            root = _artifacts_root()
            if root is None:
                return [
                    self._missing_host_capability_event(
                        phase="pre",
                        reason=("missing ARTIFACTS_ROOT for glob-based " "host artifact oracle"),
                    )
                ]
            targets = _find_matching(str(self._glob))

        for p in targets:
            try:
                if p.exists():
                    p.unlink()
                    removed.append(str(p))
            except Exception as e:  # pragma: no cover
                errors.append(f"{p}: {e}")

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="pre",
                queries=[
                    make_query(
                        query_type="host_file",
                        path=str(self._path or self._glob or ""),
                        timeout_ms=0,
                        serial=None,
                        op="clear_before_run",
                    )
                ],
                result_for_digest={"removed": removed, "errors": errors},
                result_preview={"removed_count": len(removed), "errors": errors[:3]},
                anti_gaming_notes=[
                    (
                        "Pollution control: pre_check deletes stale host callback artifacts "
                        "to prevent false positives."
                    ),
                ],
                decision=make_decision(
                    success=not errors,
                    score=1.0 if not errors else 0.0,
                    reason="cleared artifacts" if not errors else "failed to clear some artifacts",
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        if ctx.episode_time is None:
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(self._path or self._glob or "ARTIFACTS_ROOT"),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: reads a host-side callback artifact; requires an "
                            "episode time anchor to enforce a time window and avoid stale passes."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing episode time anchor (time window unavailable)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["episode_time_anchor"],
                )
            ]

        host_window = ctx.episode_time.host_window()

        candidate: Optional[Path]
        if self._path is not None:
            p = Path(self._path)
            if not p.is_absolute() and _artifacts_root() is None:
                return [
                    self._missing_host_capability_event(
                        phase="post",
                        reason="missing ARTIFACTS_ROOT for relative host artifact path",
                    )
                ]
            candidate = _resolve_path(self._path)
        else:
            root = _artifacts_root()
            if root is None:
                return [
                    self._missing_host_capability_event(
                        phase="post",
                        reason=("missing ARTIFACTS_ROOT for glob-based " "host artifact oracle"),
                    )
                ]
            candidate = _pick_latest_in_window(_find_matching(str(self._glob)), host_window)

        if candidate is None or not candidate.exists() or not candidate.is_file():
            result = {
                "path": str(candidate) if candidate else None,
                "exists": False,
                "host_window": {
                    "start_ms": host_window.start_ms,
                    "end_ms": host_window.end_ms,
                    "t0_ms": host_window.t0_ms,
                    "slack_ms": host_window.slack_ms,
                },
            }
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(candidate) if candidate else str(self._glob or ""),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        (
                            "Hard oracle: checks a host-side callback artifact file; robust "
                            "to UI spoofing."
                        ),
                        (
                            "Time window: only artifacts created during the episode window "
                            "are considered (prevents stale/historical false positives)."
                        ),
                        (
                            "Pollution control: should be paired with pre_check clearing "
                            "when snapshots are disabled."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing host artifact json in episode time window",
                        conclusive=True,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        mtime_ms = _stat_mtime_ms(candidate)
        within_window = host_window.contains(mtime_ms)
        if not within_window:
            result = {
                "path": str(candidate),
                "exists": True,
                "mtime_ms": mtime_ms,
                "within_time_window": False,
                "host_window": {
                    "start_ms": host_window.start_ms,
                    "end_ms": host_window.end_ms,
                    "t0_ms": host_window.t0_ms,
                    "slack_ms": host_window.slack_ms,
                },
            }
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(candidate),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        (
                            "Time window: artifact exists but is outside the episode window "
                            "(treated as stale to prevent false positives)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="host artifact stale (outside episode time window)",
                        conclusive=True,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        data = candidate.read_bytes()
        sha256 = stable_sha256(data)

        rel_name = candidate.name or "host_artifact.json"
        rel_name = rel_name.replace("/", "_")
        artifact_rel = Path("oracle_artifacts") / f"host_artifact_post_{rel_name}"
        artifact, artifact_error = _write_bytes_artifact(
            ctx,
            rel_path=artifact_rel,
            data=data,
            mime="application/json",
        )

        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception as e:
            result = {
                "path": str(candidate),
                "sha256": sha256,
                "parse_error": repr(e),
                "artifact": artifact,
                "artifact_error": artifact_error,
            }
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str(candidate),
                            timeout_ms=0,
                            serial=None,
                        )
                    ],
                    result_for_digest=result,
                    result_preview={k: result[k] for k in ("path", "sha256", "parse_error")},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: reads a host artifact json file; parsing failures "
                            "are treated as inconclusive."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="host artifact json parse failed",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    artifacts=[artifact] if artifact is not None else None,
                )
            ]

        matched_fields, mismatches = _match_expected(obj, self._expected)

        ok = not mismatches
        result = {
            "path": str(candidate),
            "sha256": sha256,
            "mtime_ms": mtime_ms,
            "within_time_window": within_window,
            "host_window": {
                "start_ms": host_window.start_ms,
                "end_ms": host_window.end_ms,
                "t0_ms": host_window.t0_ms,
                "slack_ms": host_window.slack_ms,
            },
            "expected": self._expected,
            "matched_fields": matched_fields,
            "mismatches": mismatches,
            "artifact": artifact,
            "artifact_error": artifact_error,
        }
        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[
                    make_query(
                        query_type="host_file",
                        path=str(candidate),
                        timeout_ms=0,
                        serial=None,
                    )
                ],
                result_for_digest={**result, "json": obj},
                result_preview=result,
                anti_gaming_notes=[
                    "Hard oracle: checks host-side callback artifact; robust to UI spoofing.",
                    (
                        "Time window: only artifacts created during the episode window "
                        "are considered (prevents stale/historical false positives)."
                    ),
                    (
                        "Pollution control: use pre_check clearing or mtime windows "
                        "to avoid stale artifact passes."
                    ),
                ],
                decision=make_decision(
                    success=ok,
                    score=1.0 if ok else 0.0,
                    reason="matched expected fields" if ok else "expected fields did not match",
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
                artifacts=[artifact] if artifact is not None else None,
            )
        ]


@register_oracle(HostArtifactJsonOracle.oracle_id)
def _make_host_artifact_json(cfg: Mapping[str, Any]) -> Oracle:
    path = cfg.get("path")
    glob = cfg.get("glob") or cfg.get("pattern")
    expected = cfg.get("expected") or cfg.get("expect") or {}
    clear_before_run = bool(cfg.get("clear_before_run", False) or cfg.get("clear_artifacts", False))
    if expected is None:
        expected = {}
    if not isinstance(expected, Mapping):
        raise ValueError("expected/expect must be an object")
    return HostArtifactJsonOracle(
        path=str(path) if path is not None else None,
        glob=str(glob) if glob is not None else None,
        expected=dict(expected),
        clear_before_run=clear_before_run,
    )


@register_oracle("HostArtifactJsonOracle")
def _make_host_artifact_json_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_host_artifact_json(cfg)
