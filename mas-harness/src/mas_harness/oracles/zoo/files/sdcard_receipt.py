"""SDCard JSON receipt oracles.

Phase 2 Step 11 (Oracle Zoo v1, D 类): File-based Receipt Oracles.

Implements `SdcardJsonReceiptOracle`:
- Optionally clears a stale receipt file in `pre_check` (pollution control).
- Pulls a JSON receipt from a fixed sdcard path (non-root) into the episode bundle.
- Matches expected fields + token within a strict episode time window.
"""

from __future__ import annotations

import json
import shlex
import tempfile
from dataclasses import dataclass
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
from mas_harness.oracles.zoo.utils.time_window import TimeWindow, parse_epoch_time_ms


def _run_adb_shell(controller: Any, *, cmd: str, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"cmd": cmd, "timeout_ms": int(timeout_ms)}
    if not hasattr(controller, "adb_shell"):
        meta["missing"] = ["adb_shell"]
        meta["returncode"] = None
        meta["stdout"] = ""
        meta["stderr"] = None
        meta["args"] = None
        return meta

    try:
        try:
            res = controller.adb_shell(cmd, timeout_ms=timeout_ms, check=False)
        except TypeError:
            res = controller.adb_shell(cmd, timeout_s=float(timeout_ms) / 1000.0, check=False)
    except TypeError:
        res = controller.adb_shell(cmd)
    except Exception as e:  # pragma: no cover
        meta["exception"] = repr(e)
        meta["returncode"] = None
        meta["stdout"] = ""
        meta["stderr"] = None
        meta["args"] = None
        return meta

    if hasattr(res, "stdout") or hasattr(res, "returncode"):
        meta.update(
            {
                "args": getattr(res, "args", None),
                "returncode": getattr(res, "returncode", None),
                "stderr": getattr(res, "stderr", None),
                "stdout": str(getattr(res, "stdout", "") or ""),
            }
        )
        return meta

    meta.update({"returncode": 0, "stdout": str(res), "stderr": None, "args": None})
    return meta


def _adb_meta_ok(meta: Mapping[str, Any]) -> bool:
    if meta.get("exception"):
        return False
    rc = meta.get("returncode")
    if isinstance(rc, int) and rc != 0:
        return False
    stdout = str(meta.get("stdout", "") or "")
    stderr = str(meta.get("stderr", "") or "")
    lowered = (stdout + "\n" + stderr).lower()
    if "permission denial" in lowered or "securityexception" in lowered:
        return False
    if lowered.strip().startswith("error:"):
        return False
    return True


def _looks_missing_file(meta: Mapping[str, Any]) -> bool:
    if _adb_meta_ok(meta):
        return False
    stdout = str(meta.get("stdout", "") or "")
    stderr = str(meta.get("stderr", "") or "")
    lowered = (stdout + "\n" + stderr).lower()
    return "no such file" in lowered or "not found" in lowered


def _probe_remote_mtime_ms(
    controller: Any, *, remote_path: str, timeout_ms: int
) -> Tuple[Optional[int], Dict[str, Any]]:
    quoted = shlex.quote(remote_path)
    attempts: list[dict[str, Any]] = []
    for cmd in (f"stat -c %Y {quoted}", f"toybox stat -c %Y {quoted}"):
        meta = _run_adb_shell(controller, cmd=cmd, timeout_ms=timeout_ms)
        parsed = parse_epoch_time_ms(str(meta.get("stdout", "") or "").strip())
        attempts.append(
            {
                "cmd": cmd,
                "meta": {k: v for k, v in meta.items() if k != "stdout"},
                "parsed": parsed,
            }
        )
        if _adb_meta_ok(meta) and parsed is not None:
            return parsed, {"attempts": attempts, "ok": True}
        if _looks_missing_file(meta):
            return None, {"attempts": attempts, "ok": False, "missing_file": True}
    return None, {"attempts": attempts, "ok": False}


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


@dataclass(frozen=True)
class _TokenMatch:
    ok: bool
    detail: Dict[str, Any]


def _match_token(
    obj: Any, *, token: Optional[str], token_path: Optional[str], token_match: str
) -> _TokenMatch:
    if token is None or token == "":
        return _TokenMatch(ok=True, detail={"enabled": False})

    mode = str(token_match or "equals").strip().lower()
    if token_path:
        found, got = _get_by_path(obj, str(token_path))
        got_str = "" if got is None else str(got)
        if mode == "contains":
            ok = token in got_str
        else:
            ok = got_str == token
        return _TokenMatch(
            ok=ok,
            detail={
                "enabled": True,
                "token_path": token_path,
                "mode": mode,
                "found": found,
                "got_preview": got_str[:120],
            },
        )

    # Fallback: search entire canonical JSON string.
    haystack = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    ok = token in haystack if mode in {"contains", "equals"} else token in haystack
    return _TokenMatch(
        ok=ok,
        detail={
            "enabled": True,
            "token_path": None,
            "mode": "contains",
            "haystack_sha256": stable_sha256(haystack),
        },
    )


def _extract_time_ms(
    obj: Any,
    *,
    timestamp_path: Optional[str],
    fallback_mtime_ms: Optional[int],
) -> Tuple[Optional[int], str]:
    if timestamp_path:
        found, raw = _get_by_path(obj, str(timestamp_path))
        if found and raw is not None:
            parsed = parse_epoch_time_ms(str(raw))
            if parsed is not None:
                return parsed, "json"
    if fallback_mtime_ms is not None:
        return int(fallback_mtime_ms), "mtime"
    return None, "missing"


def _window_meta(window: TimeWindow) -> Dict[str, Any]:
    return {
        "start_ms": window.start_ms,
        "end_ms": window.end_ms,
        "t0_ms": window.t0_ms,
        "slack_ms": window.slack_ms,
    }


class SdcardJsonReceiptOracle(Oracle):
    oracle_id = "sdcard_json_receipt"
    oracle_name = "sdcard_json_receipt"
    oracle_type = "hard"
    capabilities_required = ("adb_shell", "pull_file")

    def __init__(
        self,
        *,
        remote_path: str,
        expected: Optional[Mapping[str, Any]] = None,
        token: Optional[str] = None,
        token_path: Optional[str] = "token",
        token_match: str = "equals",
        timestamp_path: Optional[str] = "ts_ms",
        use_file_mtime_fallback: bool = True,
        clear_before_run: bool = True,
        timeout_ms: int = 15_000,
    ) -> None:
        if not isinstance(remote_path, str) or not remote_path.strip():
            raise ValueError("SdcardJsonReceiptOracle requires non-empty remote_path string")

        self._remote_path = str(remote_path)
        self._expected = dict(expected) if expected else {}
        self._token = str(token) if token is not None else None
        self._token_path = str(token_path) if token_path is not None else None
        self._token_match = str(token_match or "equals")
        self._timestamp_path = str(timestamp_path) if timestamp_path is not None else None
        self._use_mtime_fallback = bool(use_file_mtime_fallback)
        self._clear = bool(clear_before_run)
        self._timeout_ms = int(timeout_ms)

    def _missing_cap_event(
        self, *, phase: str, missing: Sequence[str], reason: str
    ) -> Dict[str, Any]:
        return make_oracle_event(
            ts_ms=now_ms(),
            oracle_id=self.oracle_id,
            oracle_name=self.oracle_name,
            oracle_type=self.oracle_type,
            phase=phase,
            queries=[
                make_query(
                    query_type="file_pull",
                    path=self._remote_path,
                    timeout_ms=self._timeout_ms,
                    serial=None,
                )
            ],
            result_for_digest={"missing": list(missing), "reason": reason},
            anti_gaming_notes=[
                (
                    "Hard oracle: reads a receipt JSON from /sdcard and matches fields/token "
                    "within an episode time window (UI spoof-resistant)."
                ),
            ],
            decision=make_decision(
                success=False,
                score=0.0,
                reason=reason,
                conclusive=False,
            ),
            capabilities_required=list(self.capabilities_required),
            missing_capabilities=list(missing),
        )

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        if not self._clear:
            return []

        if not hasattr(ctx.controller, "adb_shell"):
            return [
                self._missing_cap_event(
                    phase="pre",
                    missing=["adb_shell"],
                    reason="missing controller capability: adb_shell",
                )
            ]

        cmd = f"rm -f {shlex.quote(self._remote_path)}"
        meta = _run_adb_shell(ctx.controller, cmd=cmd, timeout_ms=max(2000, self._timeout_ms))
        ok = _adb_meta_ok(meta)
        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="pre",
                queries=[
                    make_query(
                        query_type="adb_cmd",
                        cmd=f"shell {cmd}",
                        timeout_ms=max(2000, self._timeout_ms),
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={"remote_path": self._remote_path, "rm": meta},
                result_preview={"ok": ok, "remote_path": self._remote_path},
                anti_gaming_notes=[
                    (
                        "Pollution control: pre_check deletes stale /sdcard receipts to prevent "
                        "historical false positives when snapshots are disabled."
                    ),
                ],
                decision=make_decision(
                    success=ok,
                    score=1.0 if ok else 0.0,
                    reason="cleared receipt path" if ok else "failed to clear receipt path",
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller

        missing: list[str] = []
        if not hasattr(controller, "adb_shell"):
            missing.append("adb_shell")
        if not hasattr(controller, "pull_file"):
            missing.append("pull_file")
        if missing:
            return [
                self._missing_cap_event(
                    phase="post",
                    missing=missing,
                    reason="missing controller capability: " + ", ".join(missing),
                )
            ]

        if ctx.episode_time is None:
            return [
                self._missing_cap_event(
                    phase="post",
                    missing=["episode_time_anchor"],
                    reason="missing episode time anchor (time window unavailable)",
                )
            ]

        window, window_meta = ctx.episode_time.device_window(controller=controller)
        if window is None:
            return [
                self._missing_cap_event(
                    phase="post",
                    missing=["device_time_window"],
                    reason="failed to compute device time window",
                )
            ]

        # Best-effort mtime probe (used as fallback time source when the receipt lacks a timestamp).
        fallback_mtime_ms: Optional[int] = None
        mtime_ms, mtime_meta = _probe_remote_mtime_ms(
            controller, remote_path=self._remote_path, timeout_ms=min(3000, self._timeout_ms)
        )
        if mtime_meta.get("missing_file") is True:
            result = {
                "remote_path": self._remote_path,
                "exists": False,
                "device_window": _window_meta(window),
                "device_window_meta": window_meta,
                "mtime_probe": mtime_meta,
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
                            query_type="adb_cmd",
                            cmd=f"shell stat -c %Y {shlex.quote(self._remote_path)}",
                            timeout_ms=min(3000, self._timeout_ms),
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        "Hard oracle: requires a device-side receipt file (UI spoof-resistant).",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing receipt json file on sdcard",
                        conclusive=True,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        if self._use_mtime_fallback:
            fallback_mtime_ms = mtime_ms

        rel_name = Path(self._remote_path).name or "sdcard_receipt.json"
        rel_name = rel_name.replace("/", "_")
        artifact_rel = Path("oracle_artifacts") / f"sdcard_receipt_post_{rel_name}"

        tmp_local: Optional[Path] = None
        local_path: Path
        if ctx.episode_dir is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
            tmp.close()
            tmp_local = Path(tmp.name)
            local_path = tmp_local
        else:
            local_path = ctx.episode_dir / artifact_rel

        pull_meta: Dict[str, Any] = {
            "remote_path": self._remote_path,
            "local_path": str(local_path),
            "timeout_ms": self._timeout_ms,
        }
        try:
            try:
                res = controller.pull_file(
                    self._remote_path,
                    local_path,
                    timeout_s=float(self._timeout_ms) / 1000.0,
                    check=False,
                )
            except TypeError:
                res = controller.pull_file(self._remote_path, local_path, check=False)
        except Exception as e:  # pragma: no cover
            pull_meta["exception"] = repr(e)
            res = None

        if res is not None and (hasattr(res, "stdout") or hasattr(res, "returncode")):
            pull_meta.update(
                {
                    "args": getattr(res, "args", None),
                    "returncode": getattr(res, "returncode", None),
                    "stderr": getattr(res, "stderr", None),
                    "stdout": str(getattr(res, "stdout", "") or ""),
                }
            )

        pull_ok = bool(getattr(res, "returncode", 1) == 0) if res is not None else False
        if not pull_ok or not local_path.exists():
            if tmp_local is not None:
                try:
                    tmp_local.unlink()
                except Exception:
                    pass
            result = {
                "remote_path": self._remote_path,
                "pull_ok": pull_ok,
                "pull": pull_meta,
                "mtime_probe": mtime_meta,
                "device_window": _window_meta(window),
                "device_window_meta": window_meta,
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
                            query_type="file_pull",
                            path=self._remote_path,
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            dst_rel=artifact_rel.as_posix(),
                        )
                    ],
                    result_for_digest=result,
                    result_preview={k: result[k] for k in ("remote_path", "pull_ok")},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: pulls a receipt JSON via adb. Pull failures are treated "
                            "as inconclusive (cannot conclude absence)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="failed to pull receipt json from sdcard",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        receipt_bytes = local_path.read_bytes()
        file_sha256 = stable_file_sha256(local_path)
        artifact: Optional[Dict[str, Any]] = None
        artifact_error: Optional[str] = None
        if ctx.episode_dir is not None:
            artifact, artifact_error = _write_bytes_artifact(
                ctx, rel_path=artifact_rel, data=receipt_bytes, mime="application/json"
            )

        try:
            receipt_obj = json.loads(receipt_bytes.decode("utf-8"))
        except Exception as e:
            if tmp_local is not None:
                try:
                    tmp_local.unlink()
                except Exception:
                    pass
            result = {
                "remote_path": self._remote_path,
                "sha256": file_sha256,
                "raw_sha256": stable_sha256(receipt_bytes),
                "parse_error": repr(e),
                "device_window": _window_meta(window),
                "device_window_meta": window_meta,
                "mtime_probe": mtime_meta,
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
                            query_type="file_pull",
                            path=self._remote_path,
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            dst_rel=artifact_rel.as_posix(),
                        )
                    ],
                    result_for_digest=result,
                    result_preview={k: result[k] for k in ("remote_path", "sha256", "parse_error")},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: receipt JSON must be machine-parseable; parse failures "
                            "are treated as inconclusive."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="receipt json parse failed",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    artifacts=[artifact] if artifact is not None else None,
                )
            ]

        candidates: list[dict[str, Any]] = []
        if isinstance(receipt_obj, dict):
            candidates = [receipt_obj]
        elif isinstance(receipt_obj, list):
            candidates = [x for x in receipt_obj if isinstance(x, dict)]

        token_required = self._token is not None and self._token != ""

        checked: list[dict[str, Any]] = []
        matched_idx: Optional[int] = None
        matched_time: Optional[int] = None
        matched_receipt: Optional[dict[str, Any]] = None
        any_time_available = False

        for idx, cand in enumerate(candidates):
            matched_fields, mismatches = _match_expected(cand, self._expected)
            token_match = _match_token(
                cand, token=self._token, token_path=self._token_path, token_match=self._token_match
            )
            time_ms, time_source = _extract_time_ms(
                cand,
                timestamp_path=self._timestamp_path,
                fallback_mtime_ms=fallback_mtime_ms,
            )
            if time_ms is not None:
                any_time_available = True
            within_window = bool(time_ms is not None and window.contains(time_ms))
            ok = (not mismatches) and token_match.ok and within_window

            checked.append(
                {
                    "idx": idx,
                    "matched_fields": matched_fields,
                    "mismatches": mismatches,
                    "token": token_match.detail,
                    "within_time_window": within_window,
                    "time_ms": time_ms,
                    "time_source": time_source,
                    "ok": ok,
                }
            )

            if ok:
                if matched_time is None or (time_ms is not None and time_ms > matched_time):
                    matched_idx = idx
                    matched_time = time_ms
                    matched_receipt = cand

        if not candidates:
            conclusive = False
            success = False
            reason = "receipt json did not contain an object/list of objects"
        elif not any_time_available:
            conclusive = False
            success = False
            reason = "receipt lacks timestamp and mtime fallback unavailable"
        elif matched_receipt is not None:
            conclusive = True
            success = True
            reason = f"matched receipt (idx={matched_idx})"
        else:
            conclusive = True
            success = False
            if token_required and all(not c["token"].get("found", True) for c in checked):
                reason = "token field missing in receipt"
            else:
                reason = "no receipt entry matched expected fields/token in time window"

        result = {
            "remote_path": self._remote_path,
            "sha256": file_sha256,
            "raw_sha256": stable_sha256(receipt_bytes),
            "device_window": _window_meta(window),
            "device_window_meta": window_meta,
            "mtime_probe": mtime_meta,
            "expected": self._expected,
            "token_required": token_required,
            "timestamp_path": self._timestamp_path,
            "use_file_mtime_fallback": self._use_mtime_fallback,
            "checked": checked,
            "matched_idx": matched_idx,
            "artifact": artifact,
            "artifact_error": artifact_error,
        }

        if tmp_local is not None:
            try:
                tmp_local.unlink()
            except Exception:
                pass

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
                        cmd=f"shell stat -c %Y {shlex.quote(self._remote_path)}",
                        timeout_ms=min(3000, self._timeout_ms),
                        serial=ctx.serial,
                    ),
                    make_query(
                        query_type="file_pull",
                        path=self._remote_path,
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                        dst_rel=artifact_rel.as_posix(),
                    ),
                ],
                result_for_digest={**result, "matched_receipt": matched_receipt},
                result_preview={
                    "remote_path": self._remote_path,
                    "success": success,
                    "conclusive": conclusive,
                    "matched_idx": matched_idx,
                    "sha256": file_sha256,
                },
                anti_gaming_notes=[
                    "Hard oracle: reads a device-generated receipt file (UI spoof-resistant).",
                    (
                        "Time window: receipt timestamp (or file mtime fallback) must lie within "
                        "the episode device-time window (prevents stale/historical passes)."
                    ),
                    (
                        "Pollution control: pair with pre_check clearing to avoid reusing receipts "
                        "from previous episodes when snapshots are disabled."
                    ),
                ],
                decision=make_decision(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
                artifacts=[artifact] if artifact is not None else None,
            )
        ]


@register_oracle(SdcardJsonReceiptOracle.oracle_id)
def _make_sdcard_json_receipt(cfg: Mapping[str, Any]) -> Oracle:
    remote_path = cfg.get("remote_path") or cfg.get("path") or cfg.get("sdcard_path")
    if not isinstance(remote_path, str) or not remote_path.strip():
        raise ValueError("SdcardJsonReceiptOracle requires 'remote_path' (or 'path') string")

    expected = cfg.get("expected") or cfg.get("expect") or {}
    if expected is None:
        expected = {}
    if not isinstance(expected, Mapping):
        raise ValueError("SdcardJsonReceiptOracle expected/expect must be an object")

    token = cfg.get("token")
    token_path = cfg.get("token_path") or cfg.get("token_field") or cfg.get("token_key") or "token"
    token_match = cfg.get("token_match") or cfg.get("token_mode") or "equals"
    timestamp_path = (
        cfg.get("timestamp_path") or cfg.get("timestamp_field") or cfg.get("time_field") or "ts_ms"
    )
    use_file_mtime_fallback = bool(cfg.get("use_file_mtime_fallback", True))

    clear_before_run = bool(cfg.get("clear_before_run", True) or cfg.get("clear_receipt", False))
    timeout_ms = int(cfg.get("timeout_ms", 15_000))

    return SdcardJsonReceiptOracle(
        remote_path=str(remote_path),
        expected=dict(expected),
        token=str(token) if token is not None else None,
        token_path=str(token_path) if token_path is not None else None,
        token_match=str(token_match),
        timestamp_path=str(timestamp_path) if timestamp_path is not None else None,
        use_file_mtime_fallback=use_file_mtime_fallback,
        clear_before_run=clear_before_run,
        timeout_ms=timeout_ms,
    )


@register_oracle("SdcardJsonReceiptOracle")
def _make_sdcard_json_receipt_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_sdcard_json_receipt(cfg)
