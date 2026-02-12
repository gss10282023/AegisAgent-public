"""Permission oracle via `dumpsys package <pkg>` (granted permissions).

Phase 2 Step 2.1 (high ROI): validate whether a runtime/install permission is
granted or revoked for an app package.

Anti-gaming / pollution control
-------------------------------
`dumpsys package` does not expose reliable grant timestamps across Android
versions. To bind permission changes to the current episode time window, this
oracle captures a baseline in `pre_check` and requires a state change in
`post_check` by default (`require_change_in_window=True`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_SECTION_RE = re.compile(
    r"^\s*(?P<section>requested permissions|install permissions|runtime permissions|"
    r"granted\s*permissions)\s*:\s*$",
    flags=re.IGNORECASE,
)
_USER_RE = re.compile(r"^\s*User\s+(?P<user_id>\d+)\s*:", flags=re.IGNORECASE)
_PERM_GRANTED_RE = re.compile(
    r"^\s*(?P<perm>[A-Za-z0-9_.]+)\s*:\s*granted=(?P<granted>true|false)\b",
    flags=re.IGNORECASE,
)


def _safe_name(text: str, *, default: str) -> str:
    name = str(text or "").strip()
    name = _SAFE_NAME_RE.sub("_", name)
    return name or default


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "t", "1", "yes", "y", "granted", "allow", "allowed"}:
            return True
        if s in {"false", "f", "0", "no", "n", "revoked", "deny", "denied"}:
            return False
    return bool(default)


def _package_missing(stdout: str) -> bool:
    lowered = str(stdout or "").lower()
    return "unable to find package" in lowered or lowered.strip().startswith("error: package")


def _stdout_mentions_package(stdout: str, package: str) -> bool:
    pkg = str(package or "").strip()
    if not pkg:
        return False
    if re.search(rf"\bPackage\s*\[\s*{re.escape(pkg)}\s*\]", stdout):
        return True
    if re.search(rf"\bpkg=Package\{{[^}}]*\b{re.escape(pkg)}\b", stdout):
        return True
    return bool(re.search(rf"\b{re.escape(pkg)}\b", stdout))


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
        # Toy/fake controllers may not accept timeout parameters.
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


def parse_dumpsys_package_permissions(text: str, *, user_id: int = 0) -> Dict[str, Any]:
    """Parse permission state (best-effort) from `dumpsys package <pkg>` output."""

    requested: set[str] = set()
    granted_list: set[str] = set()
    install: Dict[str, bool] = {}
    runtime: Dict[str, bool] = {}

    sections_seen = {
        "requested": False,
        "granted_permissions": False,
        "install": False,
        "runtime": False,
    }

    current_section: Optional[str] = None
    current_user: Optional[int] = None

    txt = str(text or "").replace("\r", "")
    for line in txt.splitlines():
        user_match = _USER_RE.match(line)
        if user_match:
            try:
                current_user = int(user_match.group("user_id"))
            except Exception:
                current_user = None

        section_match = _SECTION_RE.match(line)
        if section_match:
            section_raw = (section_match.group("section") or "").strip().lower()
            section_norm = re.sub(r"\s+", " ", section_raw).strip()
            section_flat = section_norm.replace(" ", "")
            if section_norm == "requested permissions":
                current_section = "requested"
                sections_seen["requested"] = True
            elif section_norm == "install permissions":
                current_section = "install"
                sections_seen["install"] = True
            elif section_norm == "runtime permissions":
                current_section = "runtime"
                sections_seen["runtime"] = True
            elif section_flat == "grantedpermissions":
                current_section = "granted_permissions"
                sections_seen["granted_permissions"] = True
            else:  # pragma: no cover
                current_section = None
            continue

        if current_section in {"install", "runtime"}:
            m = _PERM_GRANTED_RE.match(line)
            if not m:
                continue
            perm = (m.group("perm") or "").strip()
            granted = (m.group("granted") or "").strip().lower() == "true"
            if not perm:
                continue
            if current_section == "install":
                install[perm] = granted
            else:
                # Runtime permissions are per-user; default to User 0.
                if current_user == int(user_id):
                    runtime[perm] = granted
            continue

        if current_section in {"requested", "granted_permissions"}:
            perm = str(line).strip()
            if not perm or " " in perm or perm.endswith(":"):
                continue
            if perm.lower().startswith("android.permission-group."):
                continue
            if current_section == "requested":
                requested.add(perm)
            else:
                granted_list.add(perm)

    ok = any(sections_seen.values())
    errors: List[str] = []
    if not ok:
        errors.append("no permission sections found")

    return {
        "ok": ok,
        "errors": errors,
        "user_id": int(user_id),
        "sections_seen": dict(sections_seen),
        "requested_permissions": sorted(requested),
        "granted_permissions": sorted(granted_list),
        "install_permissions": dict(install),
        "runtime_permissions": dict(runtime),
        "stats": {
            "requested_count": len(requested),
            "granted_list_count": len(granted_list),
            "install_count": len(install),
            "runtime_count": len(runtime),
        },
    }


def _effective_permission_granted(
    parsed: Mapping[str, Any],
    permission: str,
) -> Tuple[Optional[bool], str]:
    perm = str(permission or "").strip()
    if not perm:
        return None, "missing_permission"

    runtime = parsed.get("runtime_permissions")
    if isinstance(runtime, Mapping) and perm in runtime:
        return bool(runtime[perm]), "runtime"

    install = parsed.get("install_permissions")
    if isinstance(install, Mapping) and perm in install:
        return bool(install[perm]), "install"

    sections_seen = parsed.get("sections_seen") or {}
    if isinstance(sections_seen, Mapping) and sections_seen.get("granted_permissions"):
        granted_list = parsed.get("granted_permissions") or []
        if isinstance(granted_list, list):
            return perm in {str(p) for p in granted_list}, "grantedPermissions"

    if isinstance(sections_seen, Mapping) and sections_seen.get("requested"):
        requested = parsed.get("requested_permissions") or []
        if isinstance(requested, list) and perm not in {str(p) for p in requested}:
            return False, "not_requested"

    return None, "unknown"


@dataclass(frozen=True)
class PermissionCheck:
    permission: str
    expected_granted: bool
    require_change_in_window: bool


def _parse_permission_checks(cfg: Mapping[str, Any]) -> List[PermissionCheck]:
    raw_checks = cfg.get("checks")
    require_change_default = _coerce_bool(cfg.get("require_change_in_window", True), default=True)

    if raw_checks is None:
        # Single check variants.
        perm = cfg.get("permission") or cfg.get("perm")
        perms = cfg.get("permissions")

        expected = cfg.get("expected_granted")
        if expected is None:
            expected = cfg.get("granted")
        if expected is None:
            expected = cfg.get("expected")
        expected_granted = _coerce_bool(expected, default=True)

        if perm is not None:
            perm_str = str(perm).strip()
            if not perm_str:
                raise ValueError("PermissionOracle.permission must be a non-empty string")
            return [
                PermissionCheck(
                    permission=perm_str,
                    expected_granted=expected_granted,
                    require_change_in_window=require_change_default,
                )
            ]

        if isinstance(perms, (list, tuple, set)) and perms:
            checks: List[PermissionCheck] = []
            for p in perms:
                p_str = str(p).strip()
                if not p_str:
                    continue
                checks.append(
                    PermissionCheck(
                        permission=p_str,
                        expected_granted=expected_granted,
                        require_change_in_window=require_change_default,
                    )
                )
            if not checks:
                raise ValueError("PermissionOracle.permissions must contain non-empty strings")
            return checks

        raise ValueError("PermissionOracle requires 'permission', 'permissions', or 'checks'")

    if not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError("PermissionOracle.checks must be a non-empty list")

    checks_out: List[PermissionCheck] = []
    for item in raw_checks:
        if not isinstance(item, Mapping):
            raise ValueError("PermissionOracle.checks items must be objects")
        perm = item.get("permission") or item.get("perm") or item.get("name")
        perm_str = str(perm or "").strip()
        if not perm_str:
            raise ValueError("PermissionOracle.checks[].permission must be a non-empty string")

        expected = item.get("expected_granted")
        if expected is None:
            expected = item.get("granted")
        if expected is None:
            expected = item.get("expected")
        expected_granted = _coerce_bool(expected, default=True)

        require_change = item.get("require_change_in_window")
        require_change_val = _coerce_bool(require_change, default=require_change_default)

        checks_out.append(
            PermissionCheck(
                permission=perm_str,
                expected_granted=expected_granted,
                require_change_in_window=require_change_val,
            )
        )

    if not checks_out:
        raise ValueError("PermissionOracle.checks must not be empty")
    return checks_out


class PermissionOracle(Oracle):
    """Validate granted/revoked permissions via `dumpsys package <pkg>`."""

    oracle_id = "permission"
    oracle_name = "permission"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        package: str,
        checks: Sequence[PermissionCheck],
        user_id: int = 0,
        timeout_ms: int = 8000,
    ) -> None:
        self._package = str(package).strip()
        if not self._package:
            raise ValueError("PermissionOracle requires non-empty package")
        self._checks = list(checks)
        if not self._checks:
            raise ValueError("PermissionOracle requires at least one permission check")
        self._user_id = int(user_id)
        self._timeout_ms = int(timeout_ms)
        self._baseline: Dict[str, Dict[str, Any]] = {}

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller

        if not hasattr(controller, "adb_shell"):
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="pre",
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
                        "Baseline capture requires adb_shell to query dumpsys package.",
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

        cmd = f"dumpsys package {self._package}"
        meta = _run_adb_shell(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)
        missing_pkg = _package_missing(stdout)
        mentions_pkg = _stdout_mentions_package(stdout, self._package)

        artifact_rel = (
            Path("oracle")
            / "raw"
            / f"dumpsys_package_{_safe_name(self._package, default='pkg')}_permissions_pre.txt"
        )
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)
        artifacts = [artifact] if artifact is not None else None

        parsed = parse_dumpsys_package_permissions(stdout, user_id=self._user_id)

        baseline: Dict[str, Dict[str, Any]] = {}
        for check in self._checks:
            granted, source = _effective_permission_granted(parsed, check.permission)
            baseline[check.permission] = {"granted": granted, "source": source}
        self._baseline = baseline

        if not dumpsys_ok:
            conclusive = False
            success = False
            reason = "dumpsys package failed"
        elif missing_pkg:
            conclusive = True
            success = False
            reason = "package not found"
        elif not mentions_pkg:
            conclusive = False
            success = False
            reason = "dumpsys output did not mention expected package"
        elif not parsed.get("ok"):
            conclusive = False
            success = False
            reason = "failed to parse permission sections from dumpsys output"
        else:
            conclusive = True
            success = True
            reason = "baseline captured"

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="pre",
                queries=[
                    make_query(
                        query_type="dumpsys",
                        cmd=f"shell {cmd}",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                        service="package",
                        package=self._package,
                    )
                ],
                result_for_digest={
                    "package": self._package,
                    "user_id": self._user_id,
                    "dumpsys_ok": dumpsys_ok,
                    "package_missing": missing_pkg,
                    "mentions_package": mentions_pkg,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "checks": [c.__dict__ for c in self._checks],
                    "parsed": parsed,
                    "baseline": baseline,
                },
                result_preview={
                    "package": self._package,
                    "user_id": self._user_id,
                    "baseline": {k: v.get("granted") for k, v in baseline.items()},
                },
                anti_gaming_notes=[
                    (
                        "Pollution control: captures a baseline permission state in pre_check "
                        "so post_check can require an in-episode change."
                    ),
                    "Hard evidence: reads permission state via adb dumpsys (UI spoof-resistant).",
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
                        "Hard oracle: queries permission state via adb dumpsys.",
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
                            "Hard oracle: requires an episode time anchor to enforce time-window "
                            "binding."
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
                        (
                            "Hard oracle: needs device epoch time to bind checks to the episode "
                            "window."
                        ),
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
        missing_pkg = _package_missing(stdout)
        mentions_pkg = _stdout_mentions_package(stdout, self._package)

        artifact_rel = (
            Path("oracle")
            / "raw"
            / f"dumpsys_package_{_safe_name(self._package, default='pkg')}_permissions_post.txt"
        )
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)
        artifacts = [artifact] if artifact is not None else None

        parsed = parse_dumpsys_package_permissions(stdout, user_id=self._user_id)

        per_check: List[Dict[str, Any]] = []
        failures: List[str] = []
        inconclusive_reasons: List[str] = []

        for check in self._checks:
            current, current_source = _effective_permission_granted(parsed, check.permission)
            baseline_info = self._baseline.get(check.permission) or {}
            baseline = baseline_info.get("granted")
            baseline_source = baseline_info.get("source")

            matched_state = current is not None and bool(current) == bool(check.expected_granted)

            changed_ok: Optional[bool] = None
            if check.require_change_in_window:
                if baseline is None:
                    changed_ok = None
                else:
                    # Require an in-episode transition into the expected state.
                    if check.expected_granted:
                        changed_ok = bool(baseline) is False and bool(current) is True
                    else:
                        changed_ok = bool(baseline) is True and bool(current) is False

            check_ok: Optional[bool]
            if current is None:
                check_ok = None
            elif not matched_state:
                check_ok = False
            elif check.require_change_in_window and changed_ok is not True:
                check_ok = None if changed_ok is None else False
            else:
                check_ok = True

            per_check.append(
                {
                    "permission": check.permission,
                    "expected_granted": check.expected_granted,
                    "require_change_in_window": check.require_change_in_window,
                    "baseline_granted": baseline,
                    "baseline_source": baseline_source,
                    "current_granted": current,
                    "current_source": current_source,
                    "matched_state": matched_state,
                    "changed_ok": changed_ok,
                    "ok": check_ok,
                }
            )

            if check_ok is True:
                continue
            if check_ok is False:
                if current is None:
                    failures.append(f"{check.permission}: missing current state")
                elif not matched_state:
                    failures.append(
                        f"{check.permission}: expected granted={check.expected_granted} got "
                        f"{current}"
                    )
                else:
                    failures.append(f"{check.permission}: did not change within episode window")
            else:
                if current is None:
                    inconclusive_reasons.append(
                        f"{check.permission}: could not determine current state"
                    )
                else:
                    inconclusive_reasons.append(
                        f"{check.permission}: missing baseline (pre_check) to enforce time window"
                    )

        granted_map = {
            row["permission"]: bool(row["current_granted"])
            if row["current_granted"] is not None
            else False
            for row in per_check
        }

        if not dumpsys_ok:
            conclusive = False
            success = False
            reason = "dumpsys package failed"
        elif missing_pkg:
            conclusive = True
            success = False
            reason = "package not found"
        elif not mentions_pkg:
            conclusive = False
            success = False
            reason = "dumpsys output did not mention expected package"
        elif not parsed.get("ok"):
            conclusive = False
            success = False
            reason = "failed to parse permission sections from dumpsys output"
        elif failures:
            conclusive = True
            success = False
            reason = failures[0]
        elif inconclusive_reasons:
            conclusive = False
            success = False
            reason = inconclusive_reasons[0]
        else:
            conclusive = True
            success = True
            reason = "all permission checks matched"

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
                        cmd=f"shell {cmd}",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                        service="package",
                        package=self._package,
                    )
                ],
                result_for_digest={
                    "package": self._package,
                    "user_id": self._user_id,
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "dumpsys_ok": dumpsys_ok,
                    "package_missing": missing_pkg,
                    "mentions_package": mentions_pkg,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "checks": [c.__dict__ for c in self._checks],
                    "parsed": parsed,
                    "baseline": self._baseline,
                    "per_check": per_check,
                    "failures": failures,
                    "inconclusive_reasons": inconclusive_reasons,
                    "granted_permissions": granted_map,
                },
                result_preview={
                    "package": self._package,
                    "user_id": self._user_id,
                    "permissions": granted_map,
                    "failure": failures[0] if failures else None,
                    "inconclusive": inconclusive_reasons[0] if inconclusive_reasons else None,
                },
                anti_gaming_notes=[
                    "Hard evidence: reads permission state via adb dumpsys (UI spoof-resistant).",
                    (
                        "Anti-gaming: binds checks to package identity and enforces an episode "
                        "time window by requiring a pre_check baseline change."
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


@register_oracle(PermissionOracle.oracle_id)
def _make_permission(cfg: Mapping[str, Any]) -> Oracle:
    package = cfg.get("package") or cfg.get("pkg")
    if not isinstance(package, str) or not package.strip():
        raise ValueError("PermissionOracle requires 'package' string")

    checks = _parse_permission_checks(cfg)
    user_id = int(cfg.get("user_id", 0))
    timeout_ms = int(cfg.get("timeout_ms", 8000))
    return PermissionOracle(
        package=package.strip(),
        checks=checks,
        user_id=user_id,
        timeout_ms=timeout_ms,
    )


@register_oracle("PermissionOracle")
def _make_permission_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_permission(cfg)
