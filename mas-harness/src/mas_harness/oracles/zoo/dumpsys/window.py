"""Window manager dumpsys oracles.

Phase 3 Step 3.2 (high ROI): identify overlays / chooser / system dialogs by
parsing `dumpsys window windows`.

Because `dumpsys window windows` output varies significantly across Android
versions, parsing is best-effort and capability-gated: if we cannot extract
enough structure (focus window and/or window list), the oracle returns
`conclusive=false`.
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

_USER_RE = re.compile(r"^u\d+$")
_WINDOW_BRACE_RE = re.compile(r"Window\{(?P<inner>[^}]+)\}")

_FOCUS_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = (
    ("mCurrentFocus", re.compile(r"mCurrentFocus=Window\{(?P<inner>[^}]+)\}")),
    ("mFocusedWindow", re.compile(r"mFocusedWindow=Window\{(?P<inner>[^}]+)\}")),
    ("CurrentFocus", re.compile(r"CurrentFocus:\s*Window\{(?P<inner>[^}]+)\}")),
)

_WINDOW_LINE_RE = re.compile(r"^\s*Window\s+#\d+\s+Window\{(?P<inner>[^}]+)\}", re.MULTILINE)


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


def _title_from_window_inner(inner: str) -> Optional[str]:
    parts = [p for p in str(inner or "").strip().split() if p]
    if not parts:
        return None

    user_idx: Optional[int] = None
    for i, part in enumerate(parts):
        if _USER_RE.match(part):
            user_idx = i
            break
    if user_idx is None or user_idx + 1 >= len(parts):
        return None

    title = " ".join(parts[user_idx + 1 :]).strip()
    return title or None


def _parse_title_to_component(title: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    raw = str(title or "").strip()
    if not raw:
        return None, None, None

    first = raw.split()[0]
    if "/" not in first:
        return raw, None, None

    pkg, activity = _parse_component(first)
    return raw, pkg, activity


def parse_window_focus(text: str) -> Dict[str, Any]:
    """Parse current focus window title (best-effort)."""

    stdout = str(text or "").replace("\r", "")
    if not stdout.strip():
        return {
            "ok": False,
            "source": None,
            "title": None,
            "package": None,
            "activity": None,
            "errors": ["empty dumpsys output"],
        }

    for source, pat in _FOCUS_PATTERNS:
        m = pat.search(stdout)
        if not m:
            continue
        inner = (m.group("inner") or "").strip()
        title = _title_from_window_inner(inner) or inner
        title_raw, pkg, activity = _parse_title_to_component(title)
        return {
            "ok": bool(title_raw),
            "source": source,
            "title": title_raw,
            "package": pkg,
            "activity": activity,
            "errors": [],
        }

    return {
        "ok": False,
        "source": None,
        "title": None,
        "package": None,
        "activity": None,
        "errors": ["no focused window found in dumpsys output"],
    }


def parse_window_titles(text: str) -> Dict[str, Any]:
    """Parse a minimal list of window titles from dumpsys output (best-effort)."""

    stdout = str(text or "").replace("\r", "")
    if not stdout.strip():
        return {"ok": False, "windows": [], "errors": ["empty dumpsys output"]}

    windows: list[dict[str, Any]] = []
    for m in _WINDOW_LINE_RE.finditer(stdout):
        inner = (m.group("inner") or "").strip()
        title = _title_from_window_inner(inner) or inner
        title_raw, pkg, activity = _parse_title_to_component(title)
        if not title_raw:
            continue
        windows.append(
            {
                "title": title_raw,
                "package": pkg,
                "activity": activity,
            }
        )

    if not windows:
        # Fallback: opportunistically collect any Window{...} occurrences.
        for m in _WINDOW_BRACE_RE.finditer(stdout):
            inner = (m.group("inner") or "").strip()
            title = _title_from_window_inner(inner)
            if not title:
                continue
            title_raw, pkg, activity = _parse_title_to_component(title)
            if not title_raw:
                continue
            windows.append(
                {
                    "title": title_raw,
                    "package": pkg,
                    "activity": activity,
                }
            )

    if not windows:
        return {"ok": False, "windows": [], "errors": ["no Window{...} entries found"]}

    # De-duplicate while preserving order (dumpsys can repeat windows).
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for w in windows:
        title = str(w.get("title") or "")
        if not title or title in seen:
            continue
        seen.add(title)
        uniq.append(w)

    return {"ok": True, "windows": uniq[:200], "errors": []}


def parse_dumpsys_window_windows(text: str) -> Dict[str, Any]:
    focus = parse_window_focus(text)
    titles = parse_window_titles(text)
    ok = bool(focus.get("ok") or titles.get("ok"))
    errors: list[str] = []
    if not ok:
        errors.extend([str(e) for e in (focus.get("errors") or []) if e])
        errors.extend([str(e) for e in (titles.get("errors") or []) if e])
        if not errors:
            errors.append("failed to parse dumpsys window output")
    return {
        "ok": ok,
        "focus": focus,
        "windows": titles.get("windows") if isinstance(titles.get("windows"), list) else [],
        "errors": errors,
        "stats": {
            "focus_ok": bool(focus.get("ok")),
            "windows_ok": bool(titles.get("ok")),
            "window_count": len(titles.get("windows") or [])
            if isinstance(titles.get("windows"), list)
            else 0,
        },
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


def _run_dumpsys_window_windows(controller: Any, *, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"cmd": "dumpsys window windows", "timeout_ms": int(timeout_ms)}

    if not hasattr(controller, "adb_shell"):
        meta["missing"] = ["adb_shell"]
        return meta

    result: Any
    try:
        try:
            result = controller.adb_shell(
                "dumpsys window windows",
                timeout_ms=int(timeout_ms),
                check=False,
            )
        except TypeError:
            result = controller.adb_shell(
                "dumpsys window windows",
                timeout_s=float(timeout_ms) / 1000.0,
                check=False,
            )
    except TypeError:
        result = controller.adb_shell("dumpsys window windows")
    except Exception as e:  # pragma: no cover
        meta["exception"] = repr(e)
        meta["returncode"] = None
        meta["stdout"] = ""
        meta["stderr"] = None
        meta["args"] = None
        return meta

    meta.update(
        {
            "args": getattr(result, "args", None),
            "returncode": getattr(result, "returncode", None),
            "stderr": getattr(result, "stderr", None),
            "stdout": str(getattr(result, "stdout", "") or ""),
        }
    )
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


def _token_matches(value: str, *, token: str, mode: str) -> bool:
    if not token:
        return False
    if mode == "equals":
        return value == token
    if mode == "regex":
        try:
            return re.search(token, value) is not None
        except re.error:
            return False
    return token in value


class WindowOracle(Oracle):
    """Hard oracle: match a token against focused window and window list titles."""

    oracle_id = "window"
    oracle_name = "window"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        token: str,
        token_match: str = "contains",
        match_scope: str = "any",
        timeout_ms: int = 5_000,
    ) -> None:
        self._token = str(token)
        if not self._token:
            raise ValueError("WindowOracle requires non-empty 'token'")
        self._token_match = str(token_match or "contains").strip() or "contains"
        if self._token_match not in {"contains", "equals", "regex"}:
            raise ValueError("WindowOracle.token_match must be one of: contains, equals, regex")
        self._match_scope = str(match_scope or "any").strip() or "any"
        if self._match_scope not in {"focus", "any"}:
            raise ValueError("WindowOracle.match_scope must be one of: focus, any")
        self._timeout_ms = int(timeout_ms)

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller

        if not hasattr(controller, "adb_shell"):
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
                            cmd="shell dumpsys window windows",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="window",
                            args=["windows"],
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: reads focused window + window list via adb dumpsys "
                            "(UI spoof-resistant)."
                        ),
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
                            cmd="shell dumpsys window windows",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="window",
                            args=["windows"],
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

        meta = _run_dumpsys_window_windows(controller, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)

        artifact_rel = Path("oracle") / "raw" / "dumpsys_window_windows_post.txt"
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)

        parsed = parse_dumpsys_window_windows(stdout)
        parse_ok = bool(parsed.get("ok"))

        haystacks: list[str] = []
        focus = parsed.get("focus") if isinstance(parsed.get("focus"), dict) else {}
        focus_title = str(focus.get("title") or "")
        if focus_title:
            haystacks.append(focus_title)
        if self._match_scope == "any":
            for w in parsed.get("windows") if isinstance(parsed.get("windows"), list) else []:
                if not isinstance(w, dict):
                    continue
                title = str(w.get("title") or "")
                if title:
                    haystacks.append(title)

        matches = [
            h for h in haystacks if _token_matches(h, token=self._token, mode=self._token_match)
        ]
        matched = bool(matches)

        if not dumpsys_ok:
            conclusive = False
            success = False
            reason = "dumpsys window windows failed"
        elif not parse_ok:
            conclusive = False
            success = False
            errors = parsed.get("errors")
            detail = None
            if isinstance(errors, list) and errors:
                detail = ", ".join(str(e) for e in errors[:3] if e)
            reason = (
                f"failed to parse windows from dumpsys output ({detail})"
                if detail
                else "failed to parse windows from dumpsys output"
            )
        elif matched:
            conclusive = True
            success = True
            reason = f"matched token in {len(matches)} window(s)"
        else:
            conclusive = True
            success = False
            reason = "no matching window token found"

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
                        cmd="shell dumpsys window windows",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                        service="window",
                        args=["windows"],
                    )
                ],
                result_for_digest={
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "expected": {
                        "token": self._token,
                        "token_match": self._token_match,
                        "match_scope": self._match_scope,
                    },
                    "dumpsys_ok": dumpsys_ok,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "parsed": {
                        "ok": parse_ok,
                        "stats": parsed.get("stats"),
                        "errors": parsed.get("errors"),
                        "focus": focus,
                        "windows_sample": (parsed.get("windows") or [])[:10],
                    },
                    "matches": matches[:20],
                },
                result_preview={
                    "matched": matched,
                    "match_count": len(matches),
                    "matches": matches[:5],
                    "focus_title": focus_title,
                    "parsed_stats": parsed.get("stats"),
                    "parse_errors": parsed.get("errors"),
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: reads focused window + window list via adb dumpsys "
                        "(UI spoof-resistant)."
                    ),
                    (
                        "Conclusive gating: if dumpsys format cannot be parsed reliably, "
                        "returns inconclusive rather than guessing."
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


@register_oracle(WindowOracle.oracle_id)
def _make_window_oracle(cfg: Mapping[str, Any]) -> Oracle:
    token = cfg.get("token") or cfg.get("window_token") or cfg.get("match")
    if not isinstance(token, str) or not token:
        raise ValueError("WindowOracle requires 'token' string")
    token_match = cfg.get("token_match") or cfg.get("match_mode") or "contains"
    match_scope = cfg.get("match_scope") or cfg.get("scope") or "any"
    return WindowOracle(
        token=token,
        token_match=str(token_match),
        match_scope=str(match_scope),
        timeout_ms=int(cfg.get("timeout_ms", 5_000)),
    )


@register_oracle("WindowOracle")
def _make_window_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_window_oracle(cfg)
