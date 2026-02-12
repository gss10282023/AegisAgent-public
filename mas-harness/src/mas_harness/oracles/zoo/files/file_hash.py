"""File hash / digest oracles.

Phase 2 Step 11 (Oracle Zoo v1, D 类): File-based Receipt Oracles.

Implements `FileHashOracle`:
- Optionally clears a stale file in `pre_check` (pollution control).
- Uses device-side `stat` to enforce an episode time window via mtime.
- Pulls the file and records a stable sha256 digest (plus optional expected hash).
"""

from __future__ import annotations

import re
import shlex
import tempfile
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
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256
from mas_harness.oracles.zoo.utils.time_window import TimeWindow, parse_epoch_time_ms

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _window_meta(window: TimeWindow) -> Dict[str, Any]:
    return {
        "start_ms": window.start_ms,
        "end_ms": window.end_ms,
        "t0_ms": window.t0_ms,
        "slack_ms": window.slack_ms,
    }


def _probe_remote_stat(
    controller: Any, *, remote_path: str, timeout_ms: int
) -> Tuple[Optional[int], Optional[int], Dict[str, Any]]:
    """Return (size_bytes, mtime_ms, meta). None values indicate probe failure."""

    quoted = shlex.quote(remote_path)
    attempts: list[dict[str, Any]] = []

    for cmd in (
        f"stat -c '%s %Y' {quoted}",
        f"toybox stat -c '%s %Y' {quoted}",
        f"stat -c %s {quoted}; stat -c %Y {quoted}",
        f"toybox stat -c %s {quoted}; toybox stat -c %Y {quoted}",
    ):
        meta = _run_adb_shell(controller, cmd=cmd, timeout_ms=timeout_ms)
        stdout = str(meta.get("stdout", "") or "").strip().replace("\r", "")
        missing_file = _looks_missing_file(meta)

        parsed_size: Optional[int] = None
        parsed_mtime: Optional[int] = None

        if _adb_meta_ok(meta):
            nums = [p for p in stdout.split() if p.strip().isdigit()]
            if len(nums) >= 2:
                parsed_size = int(nums[0])
                parsed_mtime = parse_epoch_time_ms(nums[1])

        attempts.append(
            {
                "cmd": cmd,
                "ok": _adb_meta_ok(meta),
                "missing_file": missing_file,
                "stdout_preview": stdout[:120],
                "parsed_size": parsed_size,
                "parsed_mtime_ms": parsed_mtime,
                "meta": {k: v for k, v in meta.items() if k != "stdout"},
            }
        )

        if missing_file:
            return None, None, {"attempts": attempts, "missing_file": True}
        if parsed_size is not None and parsed_mtime is not None:
            return parsed_size, parsed_mtime, {"attempts": attempts, "ok": True}

    return None, None, {"attempts": attempts, "ok": False}


class FileHashOracle(Oracle):
    oracle_id = "file_hash"
    oracle_name = "file_hash"
    oracle_type = "hard"
    capabilities_required = ("adb_shell", "pull_file")

    def __init__(
        self,
        *,
        remote_path: str,
        expected_sha256: Optional[str] = None,
        clear_before_run: bool = True,
        timeout_ms: int = 15_000,
    ) -> None:
        if not isinstance(remote_path, str) or not remote_path.strip():
            raise ValueError("FileHashOracle requires non-empty remote_path string")
        self._remote_path = str(remote_path)
        self._expected_sha256 = (
            expected_sha256.lower() if isinstance(expected_sha256, str) else None
        )
        if self._expected_sha256 and not _SHA256_HEX_RE.match(self._expected_sha256):
            raise ValueError("FileHashOracle expected_sha256 must be 64 hex chars")
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
                    "Hard oracle: verifies file existence + mtime window + content hash; "
                    "more robust than pure existence checks."
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
                        "Pollution control: pre_check deletes the target file so stale artifacts "
                        "cannot satisfy the post-run oracle."
                    ),
                ],
                decision=make_decision(
                    success=ok,
                    score=1.0 if ok else 0.0,
                    reason="cleared target file path" if ok else "failed to clear target file path",
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

        size_bytes, mtime_ms, stat_meta = _probe_remote_stat(
            controller, remote_path=self._remote_path, timeout_ms=min(3000, self._timeout_ms)
        )
        if stat_meta.get("missing_file") is True:
            result = {
                "remote_path": self._remote_path,
                "exists": False,
                "device_window": _window_meta(window),
                "device_window_meta": window_meta,
                "stat_probe": stat_meta,
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
                            cmd=f"shell stat -c '%s %Y' {shlex.quote(self._remote_path)}",
                            timeout_ms=min(3000, self._timeout_ms),
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        "Hard oracle: requires the file to exist on device in the episode window.",
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing file on device",
                        conclusive=True,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        if mtime_ms is None or size_bytes is None:
            result = {
                "remote_path": self._remote_path,
                "device_window": _window_meta(window),
                "device_window_meta": window_meta,
                "stat_probe": stat_meta,
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
                            cmd=f"shell stat -c '%s %Y' {shlex.quote(self._remote_path)}",
                            timeout_ms=min(3000, self._timeout_ms),
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest=result,
                    result_preview={"remote_path": self._remote_path, "stat_ok": False},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: requires stat(size,mtime) to enforce a strict time "
                            "window; failures are treated as inconclusive."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="failed to stat file (size/mtime unavailable)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        within_window = window.contains(mtime_ms)
        if not within_window:
            result = {
                "remote_path": self._remote_path,
                "size_bytes": size_bytes,
                "mtime_ms": mtime_ms,
                "within_time_window": False,
                "device_window": _window_meta(window),
                "device_window_meta": window_meta,
                "stat_probe": stat_meta,
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
                            cmd=f"shell stat -c '%s %Y' {shlex.quote(self._remote_path)}",
                            timeout_ms=min(3000, self._timeout_ms),
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest=result,
                    result_preview=result,
                    anti_gaming_notes=[
                        (
                            "Time window: a device file outside the episode mtime window is "
                            "treated as stale to prevent historical false positives."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="file stale (outside episode time window)",
                        conclusive=True,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        rel_name = Path(self._remote_path).name or "file"
        rel_name = rel_name.replace("/", "_")
        artifact_rel = Path("oracle_artifacts") / f"file_hash_post_{rel_name}"

        tmp_local: Optional[Path] = None
        local_path: Path
        if ctx.episode_dir is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
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
                "size_bytes": size_bytes,
                "mtime_ms": mtime_ms,
                "device_window": _window_meta(window),
                "device_window_meta": window_meta,
                "stat_probe": stat_meta,
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
                    result_preview={"remote_path": self._remote_path, "pull_ok": pull_ok},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: file exists in-window but pull failed; treated as "
                            "inconclusive (cannot verify content hash)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="failed to pull file for hashing",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                )
            ]

        sha256 = stable_file_sha256(local_path)
        artifacts = None
        if ctx.episode_dir is not None:
            artifacts = [
                {
                    "path": artifact_rel.as_posix(),
                    "sha256": sha256,
                    "bytes": int(local_path.stat().st_size),
                    "mime": "application/octet-stream",
                }
            ]

        if tmp_local is not None:
            try:
                tmp_local.unlink()
            except Exception:
                pass

        hash_ok = True
        if self._expected_sha256:
            hash_ok = sha256 == self._expected_sha256

        conclusive = True
        success = bool(within_window and hash_ok)
        reason = "file hash matched" if success else "file hash mismatch"
        if not self._expected_sha256:
            reason = "file exists in window (sha256 recorded)"

        result = {
            "remote_path": self._remote_path,
            "size_bytes": size_bytes,
            "mtime_ms": mtime_ms,
            "within_time_window": within_window,
            "sha256": sha256,
            "expected_sha256": self._expected_sha256,
            "hash_ok": hash_ok,
            "device_window": _window_meta(window),
            "device_window_meta": window_meta,
            "stat_probe": stat_meta,
            "pull": pull_meta,
            "artifact_rel": artifact_rel.as_posix(),
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
                        cmd=f"shell stat -c '%s %Y' {shlex.quote(self._remote_path)}",
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
                result_for_digest=result,
                result_preview={
                    "remote_path": self._remote_path,
                    "size_bytes": size_bytes,
                    "mtime_ms": mtime_ms,
                    "sha256": sha256,
                    "hash_ok": hash_ok,
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: requires the file to be created/modified in the episode "
                        "mtime window (prevents stale/historical passes)."
                    ),
                    (
                        "Evidence: records file sha256 and stores the pulled file as an artifact "
                        "for auditability."
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


@register_oracle(FileHashOracle.oracle_id)
def _make_file_hash(cfg: Mapping[str, Any]) -> Oracle:
    remote_path = cfg.get("remote_path") or cfg.get("path")
    if not isinstance(remote_path, str) or not remote_path.strip():
        raise ValueError("FileHashOracle requires 'remote_path' (or 'path') string")

    expected_sha256 = cfg.get("expected_sha256") or cfg.get("sha256") or cfg.get("expected_hash")
    if expected_sha256 is not None and not isinstance(expected_sha256, str):
        raise ValueError("FileHashOracle expected_sha256/sha256 must be a string")

    clear_before_run = bool(cfg.get("clear_before_run", True))
    timeout_ms = int(cfg.get("timeout_ms", 15_000))

    return FileHashOracle(
        remote_path=str(remote_path),
        expected_sha256=str(expected_sha256) if expected_sha256 is not None else None,
        clear_before_run=clear_before_run,
        timeout_ms=timeout_ms,
    )


@register_oracle("FileHashOracle")
def _make_file_hash_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_file_hash(cfg)
