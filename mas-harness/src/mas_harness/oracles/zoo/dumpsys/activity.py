"""Activity manager dumpsys oracles.

Phase 3 Step 3.1 (high ROI): validate "arrived at a page" style tasks by
parsing the *currently resumed* activity from `dumpsys activity activities`.

This oracle is intentionally minimal and designed to be composed with UI-token
matching (see Step 3.3) for stronger anti-spoofing guarantees.
"""

from __future__ import annotations

import re
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

_COMPONENT_RE = r"(?P<component>[\w.]+/[\w.$]+)"

_COMPONENT_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = (
    ("mResumedActivity", re.compile(rf"\bmResumedActivity:.*?\s{_COMPONENT_RE}\b")),
    (
        "ResumedActivity",
        re.compile(rf"\bResumedActivity:\s*ActivityRecord\{{.*?\s{_COMPONENT_RE}\b"),
    ),
    ("Resumed", re.compile(rf"\bResumed:\s*ActivityRecord\{{.*?\s{_COMPONENT_RE}\b")),
    (
        "topResumedActivity",
        re.compile(rf"\btopResumedActivity=ActivityRecord\{{.*?\s{_COMPONENT_RE}\b"),
    ),
    ("mFocusedActivity", re.compile(rf"\bmFocusedActivity:.*?\s{_COMPONENT_RE}\b")),
    ("mFocusedApp", re.compile(rf"\bmFocusedApp=ActivityRecord\{{.*?\s{_COMPONENT_RE}\b")),
    # Fallbacks: sometimes present in `dumpsys activity activities`.
    ("mCurrentFocus", re.compile(rf"\bmCurrentFocus=Window\{{.*?\s{_COMPONENT_RE}\}}")),
)


def _parse_component(component: str) -> Tuple[Optional[str], Optional[str]]:
    component = str(component or "").strip()
    if "/" not in component:
        return None, None
    pkg, activity = component.split("/", 1)
    pkg = pkg.strip()
    activity = activity.strip()
    if not pkg or not activity:
        return None, None
    if activity.startswith("."):
        activity = pkg + activity
    return pkg, activity


def parse_resumed_activity(text: str) -> Dict[str, Any]:
    """Parse a minimal view of the current resumed activity (best-effort)."""

    stdout = str(text or "").replace("\r", "")
    if not stdout.strip():
        return {
            "ok": False,
            "component": None,
            "package": None,
            "activity": None,
            "source": None,
            "candidates": [],
            "errors": ["empty dumpsys output"],
        }

    candidates: list[dict[str, Any]] = []
    for source, pat in _COMPONENT_PATTERNS:
        m = pat.search(stdout)
        if not m:
            continue
        component = (m.group("component") or "").strip()
        if not component:
            continue
        pkg, activity = _parse_component(component)
        candidates.append(
            {
                "source": source,
                "component": component,
                "package": pkg,
                "activity": activity,
            }
        )

    if not candidates:
        return {
            "ok": False,
            "component": None,
            "package": None,
            "activity": None,
            "source": None,
            "candidates": [],
            "errors": ["no resumed activity component found in dumpsys output"],
        }

    first = candidates[0]
    return {
        "ok": bool(first.get("package") and first.get("activity")),
        "component": first.get("component"),
        "package": first.get("package"),
        "activity": first.get("activity"),
        "source": first.get("source"),
        "candidates": candidates[:10],
        "errors": (
            [] if first.get("package") and first.get("activity") else ["failed to parse component"]
        ),
    }


def _dumpsys_meta_ok(meta: Mapping[str, Any]) -> bool:
    if meta.get("exception"):
        return False
    rc = meta.get("returncode")
    if isinstance(rc, int) and rc != 0:
        return False
    stdout = str(meta.get("stdout", "") or "")
    lowered = stdout.lower()
    if "permission denial" in lowered or "securityexception" in lowered:
        return False
    if lowered.strip().startswith("error:"):
        return False
    return True


def _run_dumpsys_activity_activities(controller: Any, *, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "service": "activity",
        "args": ["activities"],
        "cmd": "dumpsys activity activities",
        "timeout_ms": int(timeout_ms),
    }

    result: Any
    try:
        # Prefer `adb_shell` because many controller.dumpsys implementations quote the
        # service name, which breaks `dumpsys <service> <args>` style invocations.
        if hasattr(controller, "adb_shell"):
            meta["api"] = "adb_shell"
            try:
                result = controller.adb_shell(
                    "dumpsys activity activities",
                    timeout_ms=int(timeout_ms),
                    check=False,
                )
            except TypeError:
                result = controller.adb_shell(
                    "dumpsys activity activities",
                    timeout_s=float(timeout_ms) / 1000.0,
                    check=False,
                )
        elif hasattr(controller, "dumpsys"):
            meta["api"] = "dumpsys"
            meta["cmd"] = "dumpsys activity"
            meta["args"] = []
            try:
                result = controller.dumpsys(
                    "activity", timeout_s=float(timeout_ms) / 1000.0, check=False
                )
            except TypeError:
                result = controller.dumpsys("activity")
        else:
            meta["api"] = None
            meta["missing"] = ["dumpsys", "adb_shell"]
            return meta
    except Exception as e:  # pragma: no cover
        meta["exception"] = repr(e)
        meta["returncode"] = None
        meta["stdout"] = ""
        meta["stderr"] = None
        meta["args"] = None
        return meta

    if hasattr(result, "stdout") or hasattr(result, "returncode"):
        meta.update(
            {
                "args": getattr(result, "args", None),
                "returncode": getattr(result, "returncode", None),
                "stderr": getattr(result, "stderr", None),
                "stdout": str(getattr(result, "stdout", "") or ""),
            }
        )
        return meta

    meta.update({"returncode": 0, "stdout": str(result), "stderr": None, "args": None})
    return meta


def _write_text_artifact(
    ctx: OracleContext,
    *,
    rel_path: Path,
    text: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if ctx.episode_dir is None:
        return None, "missing episode_dir (cannot persist artifact)"

    try:
        out_path = ctx.episode_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        sha256 = stable_file_sha256(out_path)
        return (
            {
                "path": rel_path.as_posix(),
                "sha256": sha256,
                "bytes": len(text.encode("utf-8")),
                "mime": "text/plain",
            },
            None,
        )
    except Exception as e:  # pragma: no cover
        return None, f"artifact_write_failed:{type(e).__name__}:{e}"


def _normalize_expected_activity(package: str, activity: Optional[str]) -> Optional[str]:
    if activity is None:
        return None
    raw = str(activity).strip()
    if not raw:
        return None
    if "/" in raw:
        _, act = _parse_component(raw)
        return act
    if raw.startswith("."):
        return package + raw
    return raw


class ResumedActivityOracle(Oracle):
    """Hard oracle: match the current resumed activity component."""

    oracle_id = "resumed_activity"
    oracle_name = "resumed_activity"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        package: str,
        activity: Optional[str] = None,
        timeout_ms: int = 5_000,
    ) -> None:
        self._package = str(package).strip()
        if not self._package:
            raise ValueError("ResumedActivityOracle requires non-empty 'package'")
        self._activity = _normalize_expected_activity(self._package, activity)
        self._timeout_ms = int(timeout_ms)

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller

        if not (hasattr(controller, "adb_shell") or hasattr(controller, "dumpsys")):
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="dumpsys",
                            cmd="shell dumpsys activity activities",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="activity",
                            args=["activities"],
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: reads resumed activity via adb dumpsys (UI spoof-resistant).",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing controller capability: adb_shell",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["adb_shell"],
                )
            ]

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
                            query_type="dumpsys",
                            cmd="shell dumpsys activity activities",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="activity",
                            args=["activities"],
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: requires an episode time anchor to bind evidence "
                            "collection to the shared time window."
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

        window, window_meta = ctx.episode_time.device_window(controller=controller)
        if window is None:
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="adb_cmd",
                            cmd="shell date +%s%3N",
                            timeout_ms=1500,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["device_time_window"], "probe": window_meta},
                    anti_gaming_notes=[
                        "Hard oracle: needs device epoch time to bind to a time window.",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing device_time_window (failed to compute device time window)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["device_time_window"],
                )
            ]

        meta = _run_dumpsys_activity_activities(controller, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)

        artifact_rel = Path("oracle") / "raw" / "dumpsys_activity_activities_post.txt"
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)

        parsed = parse_resumed_activity(stdout)
        parse_ok = bool(parsed.get("ok"))

        observed_pkg = parsed.get("package")
        observed_activity = parsed.get("activity")
        matched_pkg = observed_pkg == self._package
        matched_activity = matched_pkg and (
            self._activity is None or observed_activity == self._activity
        )

        if not dumpsys_ok:
            conclusive = False
            success = False
            reason = "dumpsys activity failed"
        elif not parse_ok:
            conclusive = False
            success = False
            errors = parsed.get("errors")
            detail = None
            if isinstance(errors, list) and errors:
                detail = ", ".join(str(e) for e in errors[:3] if e)
            reason = (
                f"failed to parse resumed activity from dumpsys output ({detail})"
                if detail
                else "failed to parse resumed activity from dumpsys output"
            )
        elif matched_activity:
            conclusive = True
            success = True
            reason = (
                f"matched {self._package}/{self._activity}"
                if self._activity is not None
                else f"matched package {self._package}"
            )
        else:
            conclusive = True
            success = False
            expected = (
                f"{self._package}/{self._activity}" if self._activity is not None else self._package
            )
            observed = (
                f"{observed_pkg}/{observed_activity}"
                if observed_pkg and observed_activity
                else str(parsed.get("component"))
            )
            reason = f"foreground mismatch: expected {expected}, observed {observed}"

        artifacts = [artifact] if artifact is not None else None

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[
                    make_query(
                        query_type="dumpsys",
                        cmd=f"shell {meta.get('cmd') or 'dumpsys activity activities'}",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                        service="activity",
                        args=meta.get("args")
                        if isinstance(meta.get("args"), list)
                        else ["activities"],
                    )
                ],
                result_for_digest={
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "expected": {"package": self._package, "activity": self._activity},
                    "dumpsys_ok": dumpsys_ok,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "parsed": parsed,
                    "match": {
                        "matched_pkg": matched_pkg,
                        "matched_activity": matched_activity,
                        "observed": {"package": observed_pkg, "activity": observed_activity},
                    },
                },
                result_preview={
                    "matched": bool(success),
                    "expected": {"package": self._package, "activity": self._activity},
                    "observed": {"package": observed_pkg, "activity": observed_activity},
                    "parse_ok": parse_ok,
                    "parse_source": parsed.get("source"),
                },
                anti_gaming_notes=[
                    "Hard oracle: reads resumed activity via adb dumpsys (UI spoof-resistant).",
                    (
                        "Anti-gaming: binds expected package/activity and requires a shared "
                        "episode time window anchor."
                    ),
                    (
                        "Evidence hygiene: persists raw dumpsys output and records digests + "
                        "parsed fields in oracle_trace."
                    ),
                ],
                decision=make_decision(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
                artifacts=artifacts,
            )
        ]


@register_oracle(ResumedActivityOracle.oracle_id)
def _make_resumed_activity_oracle(cfg: Mapping[str, Any]) -> Oracle:
    package = cfg.get("package") or cfg.get("pkg")
    activity = cfg.get("activity") or cfg.get("act")
    component = cfg.get("component")

    # Allow specifying `component: pkg/.Act` as a convenience.
    if isinstance(component, str) and component.strip() and not activity:
        pkg2, act2 = _parse_component(component)
        if pkg2 and act2:
            if not package:
                package = pkg2
            activity = act2

    if not isinstance(package, str) or not package.strip():
        raise ValueError("ResumedActivityOracle requires 'package' string (or 'component')")

    pkg = package.strip()
    if isinstance(component, str) and component.strip():
        pkg2, _ = _parse_component(component)
        if pkg2 and pkg2 != pkg:
            raise ValueError("ResumedActivityOracle: component package must match 'package'")

    return ResumedActivityOracle(
        package=pkg,
        activity=str(activity).strip() if isinstance(activity, str) and activity.strip() else None,
        timeout_ms=int(cfg.get("timeout_ms", 5_000)),
    )


@register_oracle("ResumedActivityOracle")
def _make_resumed_activity_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_resumed_activity_oracle(cfg)


@register_oracle("ForegroundStackOracle")
def _make_foreground_stack_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_resumed_activity_oracle(cfg)
