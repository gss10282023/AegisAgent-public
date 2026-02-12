"""Notification permission oracle (Android 13+ POST_NOTIFICATIONS).

Step 2.3 (optional, high utility): validate that an app has been granted the
runtime notification permission on Android 13+.

Data source
-----------
- `dumpsys package <pkg>`: parse runtime/install/granted permissions.

Capability gating / inconclusive
--------------------------------
If we cannot parse permission sections or cannot determine the
POST_NOTIFICATIONS state, the oracle returns `conclusive=false`.
"""

from __future__ import annotations

import re
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
from mas_harness.oracles.zoo.settings.permissions import parse_dumpsys_package_permissions
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256, stable_sha256

_POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


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
        if s in {"true", "t", "1", "yes", "y"}:
            return True
        if s in {"false", "f", "0", "no", "n"}:
            return False
    return bool(default)


def _package_missing(stdout: str) -> bool:
    lowered = str(stdout or "").lower()
    return "unable to find package" in lowered or lowered.strip().startswith("error: package")


def _stdout_mentions_package(stdout: str, package: str) -> bool:
    pkg = str(package or "").strip()
    if not pkg:
        return False
    return bool(re.search(rf"\b{re.escape(pkg)}\b", stdout))


def _dumpsys_meta_ok(meta: Mapping[str, Any]) -> bool:
    if meta.get("exception"):
        return False
    rc = meta.get("returncode")
    if isinstance(rc, int) and rc != 0:
        return False
    combined = (
        str(meta.get("stdout", "") or "") + "\n" + str(meta.get("stderr", "") or "")
    ).lower()
    if "permission denial" in combined or "securityexception" in combined:
        return False
    if combined.strip().startswith("error:"):
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


def _permission_state_from_parsed(
    parsed: Mapping[str, Any],
    *,
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

    granted_list = parsed.get("granted_permissions")
    if isinstance(granted_list, list) and perm in {str(p) for p in granted_list}:
        return True, "grantedPermissions"

    requested = parsed.get("requested_permissions")
    if isinstance(requested, list) and perm in {str(p) for p in requested}:
        # Permission exists but we couldn't determine granted state.
        return None, "requested_only"

    # Not present (likely not supported or app doesn't request it).
    return None, "not_present"


class NotificationPermissionOracle(Oracle):
    """Validate Android 13+ POST_NOTIFICATIONS runtime permission via dumpsys."""

    oracle_id = "notification_permission"
    oracle_name = "notification_permission"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        package: str,
        expected_granted: bool = True,
        require_change_in_window: bool = True,
        permission: str = _POST_NOTIFICATIONS,
        user_id: int = 0,
        timeout_ms: int = 8000,
    ) -> None:
        self._package = str(package).strip()
        if not self._package:
            raise ValueError("NotificationPermissionOracle requires non-empty package")
        self._expected_granted = bool(expected_granted)
        self._require_change_in_window = bool(require_change_in_window)
        self._permission = str(permission).strip() or _POST_NOTIFICATIONS
        self._user_id = int(user_id)
        self._timeout_ms = int(timeout_ms)
        self._baseline: Optional[bool] = None
        self._baseline_source: Optional[str] = None

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        if not self._require_change_in_window:
            return []

        controller = ctx.controller
        if not hasattr(controller, "adb_shell"):
            return []

        cmd = f"dumpsys package {self._package}"
        meta = _run_adb_shell(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)
        missing_pkg = _package_missing(stdout)
        mentions_pkg = _stdout_mentions_package(stdout, self._package)

        artifact_rel = (
            Path("oracle")
            / "raw"
            / f"dumpsys_package_{_safe_name(self._package, default='pkg')}_notif_perm_pre.txt"
        )
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)
        artifacts = [artifact] if artifact is not None else None

        parsed = parse_dumpsys_package_permissions(stdout, user_id=self._user_id)
        baseline, baseline_source = _permission_state_from_parsed(
            parsed, permission=self._permission
        )
        self._baseline = baseline
        self._baseline_source = baseline_source

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
                    "permission": self._permission,
                    "expected_granted": self._expected_granted,
                    "require_change_in_window": self._require_change_in_window,
                    "dumpsys_ok": dumpsys_ok,
                    "package_missing": missing_pkg,
                    "mentions_package": mentions_pkg,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "parsed": parsed,
                    "baseline_granted": baseline,
                    "baseline_source": baseline_source,
                },
                result_preview={
                    "package": self._package,
                    "permission": self._permission,
                    "baseline_granted": baseline,
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
                        "Hard oracle: validates notification permission via adb dumpsys.",
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
            / f"dumpsys_package_{_safe_name(self._package, default='pkg')}_notif_perm_post.txt"
        )
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)
        artifacts = [artifact] if artifact is not None else None

        parsed = parse_dumpsys_package_permissions(stdout, user_id=self._user_id)
        current, current_source = _permission_state_from_parsed(parsed, permission=self._permission)

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
        elif current is None:
            conclusive = False
            success = False
            if current_source == "not_present":
                reason = f"permission not present: {self._permission}"
            else:
                reason = f"could not determine permission state: {self._permission}"
        else:
            matched = bool(current) == bool(self._expected_granted)
            if not matched:
                conclusive = True
                success = False
                reason = f"expected granted={self._expected_granted} got {current}"
            else:
                if self._require_change_in_window:
                    if self._baseline is None:
                        conclusive = False
                        success = False
                        reason = "missing baseline (pre_check) for time-window binding"
                    else:
                        changed_ok = (bool(self._baseline) != bool(current)) and (
                            bool(current) == bool(self._expected_granted)
                        )
                        conclusive = True
                        success = changed_ok
                        reason = (
                            "permission changed within episode window"
                            if changed_ok
                            else "permission did not change within episode window"
                        )
                else:
                    conclusive = True
                    success = True
                    reason = "permission matched expected state"

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
                    "permission": self._permission,
                    "expected_granted": self._expected_granted,
                    "require_change_in_window": self._require_change_in_window,
                    "dumpsys_ok": dumpsys_ok,
                    "package_missing": missing_pkg,
                    "mentions_package": mentions_pkg,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "parsed": parsed,
                    "baseline_granted": self._baseline,
                    "baseline_source": self._baseline_source,
                    "current_granted": current,
                    "current_source": current_source,
                },
                result_preview={
                    "package": self._package,
                    "permission": self._permission,
                    "granted": current,
                },
                anti_gaming_notes=[
                    "Hard evidence: reads permission state via adb dumpsys (UI spoof-resistant).",
                    (
                        "Capability gating: if the permission state cannot be determined, "
                        "the oracle is inconclusive (prevents brittle false negatives)."
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


@register_oracle(NotificationPermissionOracle.oracle_id)
def _make_notification_permission(cfg: Mapping[str, Any]) -> Oracle:
    package = cfg.get("package") or cfg.get("pkg")
    if not isinstance(package, str) or not package.strip():
        raise ValueError("NotificationPermissionOracle requires 'package' string")

    expected = cfg.get("expected_granted")
    if expected is None:
        expected = cfg.get("granted")
    if expected is None:
        expected = cfg.get("expected")
    expected_granted = _coerce_bool(expected, default=True)

    require_change = _coerce_bool(cfg.get("require_change_in_window", True), default=True)
    permission = str(cfg.get("permission", _POST_NOTIFICATIONS))
    user_id = int(cfg.get("user_id", 0))
    timeout_ms = int(cfg.get("timeout_ms", 8000))

    return NotificationPermissionOracle(
        package=package.strip(),
        expected_granted=expected_granted,
        require_change_in_window=require_change,
        permission=permission,
        user_id=user_id,
        timeout_ms=timeout_ms,
    )


@register_oracle("NotificationPermissionOracle")
def _make_notification_permission_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_notification_permission(cfg)
