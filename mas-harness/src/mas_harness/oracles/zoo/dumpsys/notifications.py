"""Notification oracle via `dumpsys notification`.

Phase 2 Step 1.3 (high ROI): validate that a notification was posted by an app
by parsing `dumpsys notification` output.

Because `dumpsys notification` output varies across Android versions, parsing is
best-effort and capability-gated: if we cannot parse required fields (package +
posted time), the oracle must return `conclusive=false`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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
from mas_harness.oracles.zoo.utils.time_window import parse_epoch_time_ms

_RECORD_START_RE = re.compile(r"^\s*NotificationRecord", flags=re.MULTILINE)
_KEY_RE = re.compile(r"^\s*key=(?P<key>\S+)\s*$", flags=re.MULTILINE)
_POST_TIME_RE = re.compile(r"\bpostTime=(?P<ts>\d{9,})\b")
_WHEN_RE = re.compile(r"\bwhen=(?P<ts>\d{9,})\b")
_PKG_RE = re.compile(r"\bpkg=(?P<pkg>[A-Za-z0-9._]+)\b")
_SBN_PKG_RE = re.compile(r"\bStatusBarNotification\([^)]*?\bpkg=(?P<pkg>[A-Za-z0-9._]+)\b")
_NO_ACTIVE_RE = re.compile(r"\bNo active notifications\b|\bNo notifications\b", flags=re.IGNORECASE)

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_EXTRA_TITLE_KEYS: Tuple[str, ...] = ("android.title", "android.titleBig", "android.title.big")
_EXTRA_TEXT_KEYS: Tuple[str, ...] = (
    "android.text",
    "android.bigText",
    "android.subText",
    "android.infoText",
    "android.summaryText",
    "android.textLines",
)


def _safe_name(text: str, *, default: str) -> str:
    name = str(text or "").strip()
    name = _SAFE_NAME_RE.sub("_", name)
    return name or default


def _run_dumpsys(controller: Any, *, service: str, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "service": str(service),
        "cmd": f"dumpsys {service}",
        "timeout_ms": int(timeout_ms),
    }

    result: Any
    try:
        if hasattr(controller, "dumpsys"):
            meta["api"] = "dumpsys"
            try:
                result = controller.dumpsys(
                    str(service), timeout_s=float(timeout_ms) / 1000.0, check=False
                )
            except TypeError:
                result = controller.dumpsys(str(service))
        elif hasattr(controller, "adb_shell"):
            meta["api"] = "adb_shell"
            try:
                result = controller.adb_shell(
                    f"dumpsys {service}",
                    timeout_ms=int(timeout_ms),
                    check=False,
                )
            except TypeError:
                result = controller.adb_shell(
                    f"dumpsys {service}",
                    timeout_s=float(timeout_ms) / 1000.0,
                    check=False,
                )
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


def _split_notification_records(text: str) -> List[str]:
    txt = str(text or "").replace("\r", "")
    if not txt.strip():
        return []

    matches = list(_RECORD_START_RE.finditer(txt))
    if not matches:
        return []

    blocks: List[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
        block = txt[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def _extract_first_epoch_ms(block: str, *, patterns: Sequence[re.Pattern[str]]) -> Optional[int]:
    for pat in patterns:
        m = pat.search(block)
        if not m:
            continue
        raw = m.group("ts")
        parsed = parse_epoch_time_ms(raw)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _extract_package(block: str) -> Optional[str]:
    for pat in (_PKG_RE, _SBN_PKG_RE):
        m = pat.search(block)
        if m:
            pkg = (m.group("pkg") or "").strip()
            if pkg:
                return pkg
    m = _KEY_RE.search(block)
    if m:
        parts = (m.group("key") or "").split("|")
        if len(parts) >= 2 and parts[1]:
            return parts[1].strip() or None
    return None


def _extract_value_after_key(text: str, key: str) -> Optional[str]:
    pat = re.compile(
        rf"{re.escape(key)}=(?P<value>.*?)(?=,\s*[A-Za-z0-9_.]+=|\}}|\]|\n|$)",
        flags=re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        return None
    value = (m.group("value") or "").strip()
    return value or None


def parse_active_notifications(text: str) -> Dict[str, Any]:
    """Parse a minimal, version-tolerant view of active notifications."""

    stdout = str(text or "")
    record_blocks = _split_notification_records(stdout)

    if not record_blocks:
        no_active = bool(_NO_ACTIVE_RE.search(stdout))
        return {
            "ok": bool(no_active),
            "no_active": no_active,
            "records": [],
            "stats": {"record_blocks": 0, "parsed_records": 0, "errors": 0},
            "errors": ([] if no_active else ["no NotificationRecord blocks found"]),
        }

    records: List[Dict[str, Any]] = []
    errors: List[str] = []
    parsed_records = 0

    for block in record_blocks:
        key = None
        m = _KEY_RE.search(block)
        if m:
            key = (m.group("key") or "").strip() or None

        pkg = _extract_package(block)
        posted_ms = _extract_first_epoch_ms(block, patterns=(_POST_TIME_RE, _WHEN_RE))

        title: Optional[str] = None
        for k in _EXTRA_TITLE_KEYS:
            title = _extract_value_after_key(block, k)
            if title:
                break

        text_val: Optional[str] = None
        for k in _EXTRA_TEXT_KEYS:
            text_val = _extract_value_after_key(block, k)
            if text_val:
                break

        record_errors: List[str] = []
        if pkg is None:
            record_errors.append("missing package")
        if posted_ms is None:
            record_errors.append("missing posted time")

        if not record_errors:
            parsed_records += 1

        if record_errors:
            errors.extend(record_errors)

        records.append(
            {
                "package": pkg,
                "key": key,
                "posted_time_ms": posted_ms,
                "title": title,
                "text": text_val,
                "block_sha256": stable_sha256(block),
                "block_preview": block[:200],
                "parse_errors": record_errors,
            }
        )

    ok = parsed_records > 0
    return {
        "ok": ok,
        "no_active": False,
        "records": records,
        "stats": {
            "record_blocks": len(record_blocks),
            "parsed_records": parsed_records,
            "errors": len(errors),
        },
        "errors": sorted(set(errors))[:20],
    }


def _token_in_record(record: Mapping[str, Any], *, token: str) -> bool:
    if not token:
        return False
    for key in ("title", "text", "block_preview"):
        val = record.get(key)
        if isinstance(val, str) and token in val:
            return True
    return False


class NotificationOracle(Oracle):
    oracle_id = "notification"
    oracle_name = "notification"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        package: str,
        token: str,
        timeout_ms: int = 10_000,
    ) -> None:
        self._package = str(package)
        self._token = str(token)
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
                            cmd="shell dumpsys notification",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="notification",
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: reads active notifications via adb dumpsys "
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
                            cmd="shell dumpsys notification",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="notification",
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: requires an episode time anchor to apply a strict "
                            "time window and avoid stale/historical false positives."
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
                        "Hard oracle: needs device epoch time to enforce a time window.",
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

        meta = _run_dumpsys(controller, service="notification", timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)

        artifact_rel = Path("oracle") / "raw" / "dumpsys_notification_post.txt"
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)

        parsed = parse_active_notifications(stdout)
        parse_ok = bool(parsed.get("ok"))
        records = parsed.get("records") if isinstance(parsed.get("records"), list) else []

        matches: List[Dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("package") != self._package:
                continue
            posted_ms = record.get("posted_time_ms")
            if not isinstance(posted_ms, int) or not window.contains(posted_ms):
                continue
            if not _token_in_record(record, token=self._token):
                continue
            matches.append(
                {
                    "package": record.get("package"),
                    "key": record.get("key"),
                    "posted_time_ms": posted_ms,
                    "title": record.get("title"),
                    "text": record.get("text"),
                    "block_sha256": record.get("block_sha256"),
                }
            )

        matches.sort(key=lambda m: int(m["posted_time_ms"]), reverse=True)
        matched = bool(matches)

        if not dumpsys_ok:
            conclusive = False
            success = False
            reason = "dumpsys notification failed"
        elif not parse_ok:
            conclusive = False
            success = False
            parse_errors = parsed.get("errors")
            detail = None
            if isinstance(parse_errors, list) and parse_errors:
                detail = ", ".join(str(e) for e in parse_errors[:3] if e)
            reason = (
                f"failed to parse active notifications from dumpsys output ({detail})"
                if detail
                else "failed to parse active notifications from dumpsys output"
            )
        elif matched:
            conclusive = True
            success = True
            reason = f"matched {len(matches)} notification(s)"
        else:
            conclusive = True
            success = False
            reason = "no matching notifications found"

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
                        cmd="shell dumpsys notification",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                        service="notification",
                    )
                ],
                result_for_digest={
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "package": self._package,
                    "token": self._token,
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
                        "records_sample": records[:5],
                    },
                    "matches": matches,
                },
                result_preview={
                    "matched": matched,
                    "match_count": len(matches),
                    "matches": matches[:3],
                    "parsed_stats": parsed.get("stats"),
                    "parse_errors": parsed.get("errors"),
                },
                anti_gaming_notes=[
                    "Hard oracle: reads active notifications via adb dumpsys (UI spoof-resistant).",
                    (
                        "Anti-gaming: requires token match + package binding + device time window "
                        "match."
                    ),
                    (
                        "Evidence hygiene: stores raw dumpsys output as an artifact and records "
                        "only structured fields + digests in oracle_trace."
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


@register_oracle(NotificationOracle.oracle_id)
def _make_notification_oracle(cfg: Mapping[str, Any]) -> Oracle:
    package = cfg.get("package") or cfg.get("pkg")
    token = cfg.get("token") or cfg.get("text_token") or cfg.get("title_token")

    if not isinstance(package, str) or not package.strip():
        raise ValueError("NotificationOracle requires 'package' string")
    if not isinstance(token, str) or not token:
        raise ValueError("NotificationOracle requires 'token' string")

    return NotificationOracle(
        package=package.strip(),
        token=str(token),
        timeout_ms=int(cfg.get("timeout_ms", 10_000)),
    )


@register_oracle("NotificationOracle")
def _make_notification_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_notification_oracle(cfg)
