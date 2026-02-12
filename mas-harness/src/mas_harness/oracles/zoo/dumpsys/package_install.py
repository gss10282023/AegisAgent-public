"""Package install/update/version oracle via `dumpsys package <pkg>`.

Phase 2 Step 1.2 (high ROI): validate app install/update/version fields with
strict episode time-window binding to prevent historical false positives.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

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

_VERSION_NAME_RE = re.compile(r"^\s*versionName=(?P<value>.+?)\s*$", flags=re.MULTILINE)
_VERSION_CODE_RE = re.compile(r"^\s*versionCode=(?P<value>\d+)\b", flags=re.MULTILINE)
_LONG_VERSION_CODE_RE = re.compile(r"^\s*longVersionCode=(?P<value>\d+)\b", flags=re.MULTILINE)
_FIRST_INSTALL_RE = re.compile(r"^\s*firstInstallTime=(?P<value>.+?)\s*$", flags=re.MULTILINE)
_LAST_UPDATE_RE = re.compile(r"^\s*lastUpdateTime=(?P<value>.+?)\s*$", flags=re.MULTILINE)

_DUMPSYS_DATETIME_RE = re.compile(
    r"(?P<dt>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})(?:\.(?P<ms>\d{1,3}))?"
)

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(text: str, *, default: str) -> str:
    name = str(text or "").strip()
    name = _SAFE_NAME_RE.sub("_", name)
    return name or default


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


def _run_adb_shell(controller: Any, *, cmd: str, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"cmd": cmd, "timeout_ms": int(timeout_ms)}
    res: Any = None
    exc: str | None = None

    try:
        try:
            res = controller.adb_shell(cmd, timeout_ms=int(timeout_ms), check=False)
        except TypeError:
            res = controller.adb_shell(cmd, timeout_s=float(timeout_ms) / 1000.0, check=False)
    except TypeError:
        res = controller.adb_shell(cmd)
    except Exception as e:  # pragma: no cover
        exc = repr(e)

    if res is not None:
        meta.update(
            {
                "args": getattr(res, "args", None),
                "returncode": getattr(res, "returncode", None),
                "stderr": getattr(res, "stderr", None),
                "stdout": str(getattr(res, "stdout", "") or ""),
            }
        )
    else:
        meta.update({"args": None, "returncode": None, "stderr": None, "stdout": ""})

    if exc:
        meta["exception"] = exc

    return meta


def _probe_device_tz_offset_seconds(
    controller: Any, *, timeout_ms: int = 1500
) -> Tuple[Optional[int], Dict[str, Any]]:
    """Best-effort device UTC offset probe (seconds)."""

    meta: Dict[str, Any] = {"attempts": []}
    probe = _run_adb_shell(controller, cmd="date +%z", timeout_ms=timeout_ms)
    probe_meta = {k: v for k, v in probe.items() if k != "stdout"}
    meta["attempts"].append({"cmd": "date +%z", "meta": probe_meta})
    stdout = str(probe.get("stdout", "") or "").strip()

    m = re.match(r"^(?P<sign>[+-])(?P<h>\d{2}):?(?P<m>\d{2})$", stdout)
    if m:
        sign = -1 if m.group("sign") == "-" else 1
        hours = int(m.group("h"))
        minutes = int(m.group("m"))
        return sign * (hours * 3600 + minutes * 60), meta

    # Fallback: infer offset from epoch seconds vs local datetime.
    probe_epoch = _run_adb_shell(controller, cmd="date +%s", timeout_ms=timeout_ms)
    meta["attempts"].append(
        {"cmd": "date +%s", "meta": {k: v for k, v in probe_epoch.items() if k != "stdout"}}
    )
    epoch_s = parse_epoch_time_ms(str(probe_epoch.get("stdout", "") or ""))
    if epoch_s is None:
        return None, meta

    probe_local = _run_adb_shell(
        controller,
        cmd="date '+%Y-%m-%d %H:%M:%S'",
        timeout_ms=timeout_ms,
    )
    meta["attempts"].append(
        {
            "cmd": "date '+%Y-%m-%d %H:%M:%S'",
            "meta": {k: v for k, v in probe_local.items() if k != "stdout"},
        }
    )
    local_txt = str(probe_local.get("stdout", "") or "").strip()
    m2 = _DUMPSYS_DATETIME_RE.search(local_txt)
    if not m2:
        return None, meta

    dt = datetime.strptime(m2.group("dt"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    offset = int(dt.timestamp()) - int(epoch_s // 1000)
    return offset, meta


def _parse_dumpsys_time_ms(text: str, *, tz_offset_seconds: Optional[int]) -> Optional[int]:
    parsed = parse_epoch_time_ms(text)
    if parsed is not None and parsed > 0:
        return parsed

    m = _DUMPSYS_DATETIME_RE.search(str(text or ""))
    if not m or tz_offset_seconds is None:
        return None

    dt = datetime.strptime(m.group("dt"), "%Y-%m-%d %H:%M:%S")
    ms_raw = m.group("ms")
    ms = int((ms_raw or "0").ljust(3, "0")[:3])
    dt = dt.replace(microsecond=ms * 1000)
    tz = timezone(timedelta(seconds=int(tz_offset_seconds)))
    return int(dt.replace(tzinfo=tz).timestamp() * 1000)


def parse_dumpsys_package_output(text: str) -> Dict[str, Any]:
    """Parse key fields from `dumpsys package <pkg>` output (best-effort)."""

    txt = str(text or "")
    version_name: Optional[str] = None
    version_code: Optional[int] = None
    first_install_raw: Optional[str] = None
    last_update_raw: Optional[str] = None

    m = _VERSION_NAME_RE.search(txt)
    if m:
        version_name = m.group("value").strip() or None

    m = _VERSION_CODE_RE.search(txt)
    if m:
        try:
            version_code = int(m.group("value"))
        except Exception:
            version_code = None

    if version_code is None:
        m = _LONG_VERSION_CODE_RE.search(txt)
        if m:
            try:
                version_code = int(m.group("value"))
            except Exception:
                version_code = None

    m = _FIRST_INSTALL_RE.search(txt)
    if m:
        first_install_raw = m.group("value").strip() or None

    m = _LAST_UPDATE_RE.search(txt)
    if m:
        last_update_raw = m.group("value").strip() or None

    return {
        "version_name": version_name,
        "version_code": version_code,
        "first_install_time_raw": first_install_raw,
        "last_update_time_raw": last_update_raw,
    }


def _package_missing(stdout: str) -> bool:
    lowered = str(stdout or "").lower()
    return "unable to find package" in lowered or "not found" in lowered


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "t", "1", "yes", "y"}:
            return True
        if s in {"false", "f", "0", "no", "n"}:
            return False
    return bool(default)


class PackageInstallOracle(Oracle):
    oracle_id = "package_install"
    oracle_name = "package_install"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        package: str,
        expected_version_name: Optional[str] = None,
        expected_version_code: Optional[int] = None,
        require_last_update_in_window: bool = True,
        require_first_install_in_window: bool = False,
        expect_installed: bool = True,
        timeout_ms: int = 8000,
    ) -> None:
        self._package = str(package)
        self._expected_version_name = (
            str(expected_version_name).strip()
            if expected_version_name is not None and str(expected_version_name).strip()
            else None
        )
        self._expected_version_code = (
            int(expected_version_code) if expected_version_code is not None else None
        )
        self._require_last_update_in_window = bool(require_last_update_in_window)
        self._require_first_install_in_window = bool(require_first_install_in_window)
        self._expect_installed = bool(expect_installed)
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
                            cmd=f"shell dumpsys package {self._package}",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="package",
                            package=self._package,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: reads package metadata via adb dumpsys (UI spoof-resistant).",
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
                            cmd=f"shell dumpsys package {self._package}",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="package",
                            package=self._package,
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: requires an episode time anchor to enforce a strict "
                            "time window on install/update timestamps."
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

        cmd = f"dumpsys package {self._package}"
        meta = _run_adb_shell(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)

        artifact_rel = (
            Path("oracle")
            / "raw"
            / f"dumpsys_package_{_safe_name(self._package, default='pkg')}_post.txt"
        )
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)
        artifacts = [artifact] if artifact is not None else None

        parsed = parse_dumpsys_package_output(stdout)
        missing_pkg = _package_missing(stdout)

        tz_offset_seconds: Optional[int] = None
        tz_probe_meta: Dict[str, Any] | None = None
        tz_queries: list[Dict[str, Any]] = []

        raw_times = (
            parsed.get("first_install_time_raw"),
            parsed.get("last_update_time_raw"),
        )
        needs_tz = any(
            isinstance(t, str)
            and t
            and parse_epoch_time_ms(t) is None
            and _DUMPSYS_DATETIME_RE.search(t)
            for t in raw_times
        )
        if needs_tz:
            tz_offset_seconds, tz_probe_meta = _probe_device_tz_offset_seconds(controller)
            tz_queries.append(
                make_query(
                    query_type="adb_cmd",
                    cmd="shell date +%z",
                    timeout_ms=1500,
                    serial=ctx.serial,
                )
            )

        first_install_ms = _parse_dumpsys_time_ms(
            str(parsed.get("first_install_time_raw") or ""), tz_offset_seconds=tz_offset_seconds
        )
        last_update_ms = _parse_dumpsys_time_ms(
            str(parsed.get("last_update_time_raw") or ""), tz_offset_seconds=tz_offset_seconds
        )

        version_name = parsed.get("version_name")
        version_code = parsed.get("version_code")

        queries = [
            make_query(
                query_type="dumpsys",
                cmd=f"shell {cmd}",
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
                service="package",
                package=self._package,
            )
        ] + tz_queries

        if not dumpsys_ok:
            conclusive = False
            success = False
            reason = "dumpsys package failed"
        elif missing_pkg:
            conclusive = True
            success = not self._expect_installed
            if self._expect_installed:
                reason = "package not installed"
            else:
                reason = "package absent (as expected)"
        elif not self._expect_installed:
            conclusive = True
            success = False
            reason = "package installed (expected absent)"
        elif version_name is None and version_code is None:
            conclusive = False
            success = False
            reason = "failed to parse version fields from dumpsys output"
        else:
            mismatches: list[str] = []
            if (
                self._expected_version_name is not None
                and version_name != self._expected_version_name
            ):
                mismatches.append(
                    "versionName mismatch: "
                    f"got {version_name!r} expected {self._expected_version_name!r}"
                )
            if (
                self._expected_version_code is not None
                and version_code != self._expected_version_code
            ):
                mismatches.append(
                    "versionCode mismatch: "
                    f"got {version_code!r} expected {self._expected_version_code!r}"
                )

            if mismatches:
                conclusive = True
                success = False
                reason = "; ".join(mismatches)
            else:
                time_fail: Optional[str] = None
                if self._require_last_update_in_window:
                    if last_update_ms is None:
                        time_fail = (
                            "missing/unsupported lastUpdateTime (cannot enforce time window)"
                        )
                    elif not window.contains(last_update_ms):
                        time_fail = "lastUpdateTime outside episode time window"
                if time_fail is None and self._require_first_install_in_window:
                    if first_install_ms is None:
                        time_fail = (
                            "missing/unsupported firstInstallTime (cannot enforce time window)"
                        )
                    elif not window.contains(first_install_ms):
                        time_fail = "firstInstallTime outside episode time window"

                if time_fail is not None:
                    conclusive = last_update_ms is not None or first_install_ms is not None
                    success = False
                    reason = time_fail
                else:
                    conclusive = True
                    success = self._expect_installed
                    reason = "package version matched and timestamps within time window"

        result_preview: Dict[str, Any] = {
            "package": self._package,
            "expect_installed": self._expect_installed,
            "expected_version_name": self._expected_version_name,
            "expected_version_code": self._expected_version_code,
            "require_last_update_in_window": self._require_last_update_in_window,
            "require_first_install_in_window": self._require_first_install_in_window,
            "dumpsys_ok": dumpsys_ok,
            "package_missing": missing_pkg,
            "version_name": version_name,
            "version_code": version_code,
            "first_install_time_ms": first_install_ms,
            "last_update_time_ms": last_update_ms,
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
                queries=queries,
                result_for_digest={
                    "package": self._package,
                    "expect_installed": self._expect_installed,
                    "expected_version_name": self._expected_version_name,
                    "expected_version_code": self._expected_version_code,
                    "require_last_update_in_window": self._require_last_update_in_window,
                    "require_first_install_in_window": self._require_first_install_in_window,
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "dumpsys_ok": dumpsys_ok,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "tz_offset_seconds": tz_offset_seconds,
                    "tz_probe_meta": tz_probe_meta,
                    "parsed": {
                        "version_name": version_name,
                        "version_code": version_code,
                        "first_install_time_raw": parsed.get("first_install_time_raw"),
                        "last_update_time_raw": parsed.get("last_update_time_raw"),
                        "first_install_time_ms": first_install_ms,
                        "last_update_time_ms": last_update_ms,
                    },
                },
                result_preview=result_preview,
                anti_gaming_notes=[
                    "Hard oracle: reads package metadata via adb dumpsys (UI spoof-resistant).",
                    (
                        "Anti-gaming: requires expected version match and strict episode time "
                        "window binding on install/update timestamps."
                    ),
                    (
                        "Evidence hygiene: stores raw dumpsys output as an artifact and "
                        "records only structured fields + digests in oracle_trace."
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


@register_oracle(PackageInstallOracle.oracle_id)
def _make_package_install(cfg: Mapping[str, Any]) -> Oracle:
    package = cfg.get("package") or cfg.get("pkg")
    if not isinstance(package, str) or not package.strip():
        raise ValueError("PackageInstallOracle requires 'package' string")

    expected_version_name = (
        cfg.get("expected_version_name")
        or cfg.get("versionName")
        or cfg.get("version_name")
        or cfg.get("version")
    )
    expected_version_code = (
        cfg.get("expected_version_code") or cfg.get("versionCode") or cfg.get("version_code")
    )

    version_code_int: Optional[int] = None
    if expected_version_code is not None:
        try:
            version_code_int = int(expected_version_code)
        except Exception:
            raise ValueError("PackageInstallOracle expected_version_code must be an int") from None

    return PackageInstallOracle(
        package=package.strip(),
        expected_version_name=str(expected_version_name).strip()
        if isinstance(expected_version_name, str) and expected_version_name.strip()
        else None,
        expected_version_code=version_code_int,
        require_last_update_in_window=_coerce_bool(
            cfg.get("require_last_update_in_window", True), default=True
        ),
        require_first_install_in_window=_coerce_bool(
            cfg.get("require_first_install_in_window", False), default=False
        ),
        expect_installed=_coerce_bool(cfg.get("expect_installed", True), default=True),
        timeout_ms=int(cfg.get("timeout_ms", 8000)),
    )


@register_oracle("PackageInstallOracle")
def _make_package_install_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_package_install(cfg)
