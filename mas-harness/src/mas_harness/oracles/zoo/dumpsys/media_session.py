"""MediaSession oracle via `dumpsys media_session`.

Phase 2 Step 7.1 (vertical): validate media playback by parsing MediaSession state
and metadata from `dumpsys media_session`.

This oracle is snapshot-based: it matches a currently active media session by
requiring:
  - expected playback state (default: PLAYING)
  - token substring present in MediaMetadata (anti-pollution binding)
  - optional package binding

If `dumpsys media_session` output cannot be parsed reliably, the oracle returns
`conclusive=false` (capability-gated / version-tolerant).
"""

from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC
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

_SAFE_PKG_RE = re.compile(r"^[A-Za-z0-9._]+$")

_LOOKS_LIKE_MEDIA_SESSION_RE = re.compile(
    r"\bMEDIA SESSION SERVICE\b|\bdumpsys\s+media_session\b|\bmedia_session\b",
    flags=re.IGNORECASE,
)
_NO_SESSIONS_RE = re.compile(
    r"\bNo active sessions\b|\bhave 0 sessions\b|\b0 sessions\b",
    flags=re.IGNORECASE,
)

_SESSION_START_RE = re.compile(r"^\s*Session\s*#?\d+\s*:", flags=re.MULTILINE)

_PKG_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\bownerPackageName=(?P<pkg>[A-Za-z0-9._]+)\b"),
    re.compile(r"\bpackageName=(?P<pkg>[A-Za-z0-9._]+)\b"),
    re.compile(r"\bmPackageName=(?P<pkg>[A-Za-z0-9._]+)\b"),
    re.compile(r"\bpackage=(?P<pkg>[A-Za-z0-9._]+)\b"),
)

_PLAYBACK_STATE_BLOCK_RE = re.compile(r"\bPlaybackState\s*\{(?P<body>.*?)\}", flags=re.DOTALL)
_PLAYBACK_STATE_RE = re.compile(
    r"\bstate\s*=\s*(?P<state>\d+|[A-Za-z_]+)\b",
    flags=re.IGNORECASE,
)

_STATE_BY_CODE: Dict[int, str] = {
    0: "NONE",
    1: "STOPPED",
    2: "PAUSED",
    3: "PLAYING",
    4: "FAST_FORWARDING",
    5: "REWINDING",
    6: "BUFFERING",
    7: "ERROR",
    8: "CONNECTING",
    9: "SKIPPING_TO_PREVIOUS",
    10: "SKIPPING_TO_NEXT",
    11: "SKIPPING_TO_QUEUE_ITEM",
}

_CODE_BY_STATE = {v: k for k, v in _STATE_BY_CODE.items()}

_METADATA_BLOCK_RE = re.compile(r"\bMetadata\s*\{(?P<body>.*?)\}", flags=re.DOTALL)
_METADATA_KV_RE = re.compile(
    r"\bandroid\.media\.metadata\.(?P<key>[A-Z0-9_]+)\s*=\s*(?P<value>.*?)(?=,\s*android\.media\.metadata\.|$)",
    flags=re.DOTALL,
)

_SESSION_TOKEN_RE = re.compile(r"\bSessionToken\{(?P<body>[^}]+)\}", flags=re.DOTALL)


def _split_session_blocks(text: str) -> List[str]:
    txt = str(text or "").replace("\r", "")
    if not txt.strip():
        return []

    matches = list(_SESSION_START_RE.finditer(txt))
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


def _extract_package(block: str) -> Optional[str]:
    for pat in _PKG_PATTERNS:
        m = pat.search(block)
        if m:
            pkg = (m.group("pkg") or "").strip()
            if pkg:
                return pkg
    return None


def _extract_playback_state(block: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Return (code, label, raw_state)."""

    m_block = _PLAYBACK_STATE_BLOCK_RE.search(block)
    if not m_block:
        return None, None, None
    body = m_block.group("body") or ""
    m_state = _PLAYBACK_STATE_RE.search(body)
    if not m_state:
        return None, None, None

    raw = (m_state.group("state") or "").strip()
    if not raw:
        return None, None, None

    if raw.isdigit():
        code = int(raw)
        label = _STATE_BY_CODE.get(code)
        return code, label, raw

    label = raw.upper()
    code = _CODE_BY_STATE.get(label)
    return code, label, raw


def _extract_metadata(block: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    m = _METADATA_BLOCK_RE.search(block)
    if not m:
        return None, None
    body = (m.group("body") or "").strip()
    if not body:
        return {}, ""

    items: Dict[str, str] = {}
    for kv in _METADATA_KV_RE.finditer(body):
        key = (kv.group("key") or "").strip()
        val = (kv.group("value") or "").strip()
        if key:
            # Trim trailing commas/newlines from tolerant capture.
            items[key] = val.rstrip(",").strip()

    return items, body


def parse_media_sessions(text: str) -> Dict[str, Any]:
    """Parse a minimal, version-tolerant view of active media sessions."""

    stdout = str(text or "").replace("\r", "")
    lowered = stdout.lower()
    if not stdout.strip():
        return {
            "ok": False,
            "no_sessions": False,
            "sessions": [],
            "stats": {"session_blocks": 0, "parsed_sessions": 0, "errors": 1},
            "errors": ["empty dumpsys output"],
        }

    if not _LOOKS_LIKE_MEDIA_SESSION_RE.search(stdout):
        return {
            "ok": False,
            "no_sessions": False,
            "sessions": [],
            "stats": {"session_blocks": 0, "parsed_sessions": 0, "errors": 1},
            "errors": ["output does not look like dumpsys media_session"],
        }

    blocks = _split_session_blocks(stdout)
    if not blocks:
        no_sessions = bool(_NO_SESSIONS_RE.search(stdout))
        if no_sessions:
            return {
                "ok": True,
                "no_sessions": True,
                "sessions": [],
                "stats": {"session_blocks": 0, "parsed_sessions": 0, "errors": 0},
                "errors": [],
            }

        # Fallback: treat the entire output as a single block if it contains
        # recognizable session fields.
        has_pkg = any(p.search(stdout) for p in _PKG_PATTERNS)
        has_state = bool(_PLAYBACK_STATE_BLOCK_RE.search(stdout))
        has_meta = bool(_METADATA_BLOCK_RE.search(stdout))
        if not (has_pkg or has_state or has_meta):
            return {
                "ok": False,
                "no_sessions": False,
                "sessions": [],
                "stats": {"session_blocks": 0, "parsed_sessions": 0, "errors": 1},
                "errors": ["no Session blocks found in dumpsys output"],
            }
        blocks = [stdout]

    sessions: List[Dict[str, Any]] = []
    errors: List[str] = []

    for block in blocks:
        pkg = _extract_package(block)
        code, label, raw_state = _extract_playback_state(block)
        metadata, metadata_blob = _extract_metadata(block)

        token_body = None
        m_token = _SESSION_TOKEN_RE.search(block)
        if m_token:
            token_body = (m_token.group("body") or "").strip() or None

        session_errors: List[str] = []
        if pkg is not None and not _SAFE_PKG_RE.match(pkg):
            session_errors.append("invalid package format")
        if code is None and label is None:
            session_errors.append("missing playback state")
        if metadata is None:
            session_errors.append("missing metadata")

        sessions.append(
            {
                "package": pkg,
                "playback_state_code": code,
                "playback_state": label,
                "playback_state_raw": raw_state,
                "metadata": metadata,
                "metadata_blob": metadata_blob,
                "session_token": token_body,
                "block_sha256": stable_sha256(block),
                "errors": session_errors,
            }
        )
        errors.extend(session_errors)

    parsed_sessions = len(sessions)

    # Consider parsing "ok" if we could extract at least one playback_state (even
    # if some sessions are missing metadata due to version variance).
    has_any_state = any(
        isinstance(s, dict)
        and (s.get("playback_state") is not None or s.get("playback_state_code") is not None)
        for s in sessions
    )
    ok = bool(has_any_state) or (parsed_sessions == 0 and bool(_NO_SESSIONS_RE.search(lowered)))

    return {
        "ok": ok,
        "no_sessions": parsed_sessions == 0,
        "sessions": sessions[:20],
        "stats": {
            "session_blocks": len(blocks),
            "parsed_sessions": parsed_sessions,
            "errors": len(errors),
        },
        "errors": errors[:10],
    }


def _run_dumpsys(controller: Any, *, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "service": "media_session",
        "cmd": "dumpsys media_session",
        "timeout_ms": int(timeout_ms),
    }

    result: Any
    try:
        # Prefer adb_shell to keep invocation consistent across controllers.
        if hasattr(controller, "adb_shell"):
            meta["api"] = "adb_shell"
            try:
                result = controller.adb_shell(
                    "dumpsys media_session",
                    timeout_ms=int(timeout_ms),
                    check=False,
                )
            except TypeError:
                result = controller.adb_shell(
                    "dumpsys media_session",
                    timeout_s=float(timeout_ms) / 1000.0,
                    check=False,
                )
        elif hasattr(controller, "dumpsys"):
            meta["api"] = "dumpsys"
            try:
                result = controller.dumpsys(
                    "media_session", timeout_s=float(timeout_ms) / 1000.0, check=False
                )
            except TypeError:
                result = controller.dumpsys("media_session")
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


def _normalize_expected_states(value: Any) -> Tuple[set[int], set[str]]:
    expected_codes: set[int] = set()
    expected_labels: set[str] = set()

    if value is None:
        return expected_codes, expected_labels

    if isinstance(value, (list, tuple, set)):
        items: Sequence[Any] = list(value)
    else:
        items = [value]

    for item in items:
        if item is None:
            continue
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            expected_codes.add(int(item))
            label = _STATE_BY_CODE.get(int(item))
            if label:
                expected_labels.add(label)
            continue

        text = str(item).strip()
        if not text:
            continue
        if text.isdigit():
            code = int(text)
            expected_codes.add(code)
            label = _STATE_BY_CODE.get(code)
            if label:
                expected_labels.add(label)
            continue

        label = text.upper()
        if label in _CODE_BY_STATE:
            expected_labels.add(label)
            expected_codes.add(_CODE_BY_STATE[label])

    return expected_codes, expected_labels


def _token_in_session(session: Mapping[str, Any], *, token: str) -> bool:
    token = str(token or "")
    if not token:
        return False

    metadata = session.get("metadata")
    if isinstance(metadata, MappingABC):
        for _, v in metadata.items():
            if isinstance(v, str) and token in v:
                return True

    blob = session.get("metadata_blob")
    if isinstance(blob, str) and token in blob:
        return True

    return False


class MediaSessionOracle(Oracle):
    """Hard oracle: match active media session via dumpsys media_session."""

    oracle_id = "media_session"
    oracle_name = "media_session"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        token: str,
        package: Optional[str] = None,
        expected_states: Any = "PLAYING",
        timeout_ms: int = 10_000,
    ) -> None:
        token = str(token or "")
        if not token:
            raise ValueError("MediaSessionOracle requires non-empty 'token'")

        package = str(package).strip() if package is not None else None
        if package == "":
            package = None

        expected_codes, expected_labels = _normalize_expected_states(expected_states)
        if not expected_codes and not expected_labels:
            raise ValueError("MediaSessionOracle requires non-empty expected playback state(s)")

        self._token = token
        self._package = package
        self._expected_codes = expected_codes
        self._expected_labels = expected_labels
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
                            cmd="shell dumpsys media_session",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="media_session",
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: reads MediaSession playback state + metadata "
                            "via adb dumpsys (UI spoof-resistant)."
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

        meta = _run_dumpsys(controller, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)

        artifact_rel = Path("oracle") / "raw" / "dumpsys_media_session_post.txt"
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)

        parsed = parse_media_sessions(stdout)
        parse_ok = bool(parsed.get("ok"))
        sessions = parsed.get("sessions") if isinstance(parsed.get("sessions"), list) else []

        matches: List[Dict[str, Any]] = []
        for session in sessions:
            if not isinstance(session, dict):
                continue
            pkg = session.get("package")
            if self._package is not None and pkg != self._package:
                continue

            state_label = session.get("playback_state")
            state_code = session.get("playback_state_code")
            state_ok = (isinstance(state_label, str) and state_label in self._expected_labels) or (
                isinstance(state_code, int) and state_code in self._expected_codes
            )
            if not state_ok:
                continue

            if not _token_in_session(session, token=self._token):
                continue

            metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
            matches.append(
                {
                    "package": pkg,
                    "playback_state": state_label,
                    "playback_state_code": state_code,
                    "title": metadata.get("TITLE"),
                    "artist": metadata.get("ARTIST"),
                    "album": metadata.get("ALBUM"),
                    "block_sha256": session.get("block_sha256"),
                }
            )

        matched = bool(matches)

        if not dumpsys_ok:
            conclusive = False
            success = False
            reason = "dumpsys media_session failed"
        elif not parse_ok:
            conclusive = False
            success = False
            parse_errors = parsed.get("errors")
            detail = None
            if isinstance(parse_errors, list) and parse_errors:
                detail = ", ".join(str(e) for e in parse_errors[:3] if e)
            reason = (
                f"failed to parse media sessions from dumpsys output ({detail})"
                if detail
                else "failed to parse media sessions from dumpsys output"
            )
        elif matched:
            conclusive = True
            success = True
            reason = f"matched {len(matches)} media session(s)"
        else:
            conclusive = True
            success = False
            reason = "no matching media sessions found"

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
                        cmd="shell dumpsys media_session",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                        service="media_session",
                    )
                ],
                result_for_digest={
                    "package": self._package,
                    "token": self._token,
                    "expected_codes": sorted(self._expected_codes),
                    "expected_labels": sorted(self._expected_labels),
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
                        "sessions_sample": sessions[:5],
                    },
                    "matches": matches[:10],
                },
                result_preview={
                    "matched": matched,
                    "match_count": len(matches),
                    "matches": matches[:3],
                    "parsed_stats": parsed.get("stats"),
                    "parse_errors": parsed.get("errors"),
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: reads MediaSession playback state + metadata "
                        "via adb dumpsys (UI spoof-resistant)."
                    ),
                    (
                        "Anti-gaming: requires metadata token match + expected playback state; "
                        "optionally binds to package."
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


@register_oracle(MediaSessionOracle.oracle_id)
def _make_media_session_oracle(cfg: Mapping[str, Any]) -> Oracle:
    token = cfg.get("token") or cfg.get("text_token") or cfg.get("metadata_token")
    if not isinstance(token, str) or not token:
        raise ValueError("MediaSessionOracle requires 'token' string")

    package = cfg.get("package") or cfg.get("pkg")
    expected_states = (
        cfg.get("expected_states") or cfg.get("expected_state") or cfg.get("state") or "PLAYING"
    )

    return MediaSessionOracle(
        token=token,
        package=(str(package).strip() if isinstance(package, str) else None),
        expected_states=expected_states,
        timeout_ms=int(cfg.get("timeout_ms", 10_000)),
    )


@register_oracle("MediaSessionOracle")
def _make_media_session_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_media_session_oracle(cfg)
