"""AppOps oracle via `appops get <pkg>`.

Phase 2 Step 2.2 (high ROI): validate system policy toggles by reading the
authoritative AppOps state from the device shell.

Because AppOps output is not fully stable across Android versions/builds, this
oracle is capability-gated: if `appops` is unavailable or the output cannot be
parsed for the requested ops, it returns `conclusive=false`.

Time window / pollution control
-------------------------------
AppOps does not reliably expose change timestamps. To bind outcomes to the
current episode window and avoid historical false positives, this oracle
captures a baseline in `pre_check` and (by default) requires a transition into
the expected mode in `post_check` (`require_change_in_window=True`).
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
from mas_harness.oracles.zoo.settings.permissions import parse_dumpsys_package_permissions
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256, stable_sha256

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_SCOPE_UID_RE = re.compile(r"^\s*(?:uid\s+mode|uid\s+\d+)\s*:\s*$", flags=re.IGNORECASE)
_SCOPE_PKG_RE = re.compile(
    r"^\s*(?:package\s+mode|package\s+[A-Za-z0-9._]+)\s*:\s*$",
    flags=re.IGNORECASE,
)

_PACKAGE_MENTION_RE = re.compile(
    r"\bpackage\b[^A-Za-z0-9._]+(?P<pkg>[A-Za-z0-9._]+)\b", flags=re.IGNORECASE
)

_OP_LINE_RE = re.compile(r"^\s*(?P<op>[A-Za-z0-9_:.+-]+)\s*:\s*(?P<mode>[A-Za-z_]+)\b")

_NO_OPS_RE = re.compile(r"\bno\s+(?:operations|ops)\b", flags=re.IGNORECASE)
_CMD_NOT_FOUND_RE = re.compile(
    r"\b(?:not found|unknown command|no such file)\b",
    flags=re.IGNORECASE,
)
_PERMISSION_DENIED_RE = re.compile(
    r"\b(?:permission denial|securityexception)\b", flags=re.IGNORECASE
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
        if s in {"true", "t", "1", "yes", "y"}:
            return True
        if s in {"false", "f", "0", "no", "n"}:
            return False
    return bool(default)


def _normalize_op_name(value: Any) -> str:
    op = str(value or "").strip()
    if ":" in op:
        op = op.split(":", 1)[1]
    return op.strip().upper()


def _normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    # Common synonym seen in some outputs.
    if mode == "ignored":
        return "ignore"
    return mode


def _normalize_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    if scope in {"uid", "uid_mode", "uidmode"}:
        return "uid"
    if scope in {"package", "pkg", "package_mode", "packagemode"}:
        return "package"
    if scope in {"any", "either", "*"}:
        return "any"
    raise ValueError("AppOpsOracle.checks[].scope must be one of: uid|package|any")


def _stdout_mentions_package(stdout: str, package: str) -> bool:
    pkg = str(package or "").strip()
    if not pkg:
        return False
    return bool(re.search(rf"\b{re.escape(pkg)}\b", stdout))


def _appops_error_kind(stdout: str) -> Optional[str]:
    text = str(stdout or "")
    if _PERMISSION_DENIED_RE.search(text):
        return "permission_denied"
    if _CMD_NOT_FOUND_RE.search(text):
        return "command_unavailable"
    lowered = text.strip().lower()
    if lowered.startswith("error:") or lowered.startswith("usage:"):
        return "command_error"
    return None


def _adb_meta_ok(meta: Mapping[str, Any]) -> bool:
    if meta.get("exception"):
        return False
    rc = meta.get("returncode")
    if isinstance(rc, int) and rc != 0:
        return False
    text = "\n".join(
        [
            str(meta.get("stdout", "") or ""),
            str(meta.get("stderr", "") or ""),
        ]
    )
    return _appops_error_kind(text) is None


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


def parse_appops_get_output(text: str) -> Dict[str, Any]:
    """Parse a minimal, version-tolerant view of `appops get <pkg>` output."""

    stdout = str(text or "").replace("\r", "")
    scope: Optional[str] = None
    ops: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    for line in stdout.splitlines():
        if _SCOPE_UID_RE.match(line):
            scope = "uid"
            continue
        if _SCOPE_PKG_RE.match(line):
            scope = "package"
            continue

        m = _OP_LINE_RE.match(line)
        if not m:
            continue

        op_raw = (m.group("op") or "").strip()
        mode_raw = (m.group("mode") or "").strip()
        if not op_raw or not mode_raw:
            continue

        op = _normalize_op_name(op_raw)
        mode = _normalize_mode(mode_raw)
        rec = ops.setdefault(op, {"op": op, "raw_names": set(), "scopes": {}, "modes": []})
        rec["raw_names"].add(op_raw)
        rec["modes"].append({"scope": scope or "unknown", "mode": mode, "line": line.strip()})
        if scope in {"uid", "package"}:
            rec["scopes"][scope] = mode

    mentioned_pkgs = sorted(
        {m.group("pkg") for m in _PACKAGE_MENTION_RE.finditer(stdout) if m.group("pkg")}
    )
    no_ops = bool(_NO_OPS_RE.search(stdout)) or not stdout.strip()

    ok = bool(ops) or no_ops
    if not ok:
        errors.append("no appops entries parsed")

    # Convert raw_names to sorted list for stable hashing.
    for rec in ops.values():
        rec["raw_names"] = sorted(rec["raw_names"])

    return {
        "ok": ok,
        "no_ops": no_ops,
        "mentioned_packages": mentioned_pkgs,
        "ops": ops,
        "errors": errors,
        "stats": {
            "op_count": len(ops),
            "mode_entries": sum(len(v.get("modes") or []) for v in ops.values()),
        },
    }


def _effective_op_mode(
    parsed: Mapping[str, Any],
    *,
    op: str,
    scope: str,
) -> Tuple[Optional[str], str]:
    ops = parsed.get("ops")
    if not isinstance(ops, Mapping):
        return None, "missing_ops"

    op_norm = _normalize_op_name(op)
    rec = ops.get(op_norm)
    if not isinstance(rec, Mapping):
        return None, "missing_op"

    scopes = rec.get("scopes")
    if not isinstance(scopes, Mapping):
        return None, "missing_scopes"

    scope_norm = _normalize_scope(scope)
    if scope_norm == "uid":
        val = scopes.get("uid")
        return (str(val) if isinstance(val, str) and val else None), "uid"
    if scope_norm == "package":
        val = scopes.get("package")
        return (str(val) if isinstance(val, str) and val else None), "package"

    # any: prefer package then uid.
    for candidate in ("package", "uid"):
        val = scopes.get(candidate)
        if isinstance(val, str) and val:
            return val, candidate
    return None, "missing_mode"


@dataclass(frozen=True)
class AppOpCheck:
    op: str
    expected_any_of: Tuple[str, ...]
    scope: str
    require_change_in_window: bool


def _parse_appop_checks(cfg: Mapping[str, Any]) -> List[AppOpCheck]:
    raw_checks = cfg.get("checks") or cfg.get("ops")
    require_change_default = _coerce_bool(cfg.get("require_change_in_window", True), default=True)

    if raw_checks is None:
        # Single check convenience form.
        op = cfg.get("op") or cfg.get("operation")
        mode = cfg.get("mode") or cfg.get("expected_mode") or cfg.get("expected")
        if op is None or mode is None:
            raise ValueError("AppOpsOracle requires 'checks' list or ('op' and 'mode')")

        scope = cfg.get("scope", "any")
        expected_values = (
            tuple(_normalize_mode(v) for v in mode)
            if isinstance(mode, (list, tuple, set))
            else (_normalize_mode(mode),)
        )
        expected_values = tuple(v for v in expected_values if v)
        if not expected_values:
            raise ValueError("AppOpsOracle.mode must not be empty")

        return [
            AppOpCheck(
                op=_normalize_op_name(op),
                expected_any_of=expected_values,
                scope=_normalize_scope(scope),
                require_change_in_window=require_change_default,
            )
        ]

    if not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError("AppOpsOracle.checks must be a non-empty list")

    checks: List[AppOpCheck] = []
    for item in raw_checks:
        if not isinstance(item, Mapping):
            raise ValueError("AppOpsOracle.checks items must be objects")

        op = item.get("op") or item.get("operation") or item.get("name")
        op_norm = _normalize_op_name(op)
        if not op_norm:
            raise ValueError("AppOpsOracle.checks[].op must be a non-empty string")

        mode = item.get("mode") or item.get("expected_mode") or item.get("expected")
        if mode is None:
            raise ValueError("AppOpsOracle.checks[] requires 'mode'")

        expected_values = (
            tuple(_normalize_mode(v) for v in mode)
            if isinstance(mode, (list, tuple, set))
            else (_normalize_mode(mode),)
        )
        expected_values = tuple(v for v in expected_values if v)
        if not expected_values:
            raise ValueError("AppOpsOracle.checks[].mode must not be empty")

        scope = _normalize_scope(item.get("scope", cfg.get("scope", "any")))
        require_change = _coerce_bool(
            item.get("require_change_in_window", require_change_default),
            default=require_change_default,
        )

        checks.append(
            AppOpCheck(
                op=op_norm,
                expected_any_of=expected_values,
                scope=scope,
                require_change_in_window=require_change,
            )
        )

    if not checks:
        raise ValueError("AppOpsOracle.checks must not be empty")
    return checks


class AppOpsOracle(Oracle):
    """Validate AppOps operation modes via `appops get`."""

    oracle_id = "appops"
    oracle_name = "appops"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        package: str,
        checks: Sequence[AppOpCheck],
        include_permission_snapshot: bool = True,
        permission_user_id: int = 0,
        timeout_ms: int = 8000,
    ) -> None:
        self._package = str(package).strip()
        if not self._package:
            raise ValueError("AppOpsOracle requires non-empty package")
        self._checks = list(checks)
        if not self._checks:
            raise ValueError("AppOpsOracle requires at least one check")
        self._include_permission_snapshot = bool(include_permission_snapshot)
        self._permission_user_id = int(permission_user_id)
        self._timeout_ms = int(timeout_ms)
        self._baseline: Dict[str, Dict[str, Any]] = {}

    def _capture_permission_snapshot(
        self,
        ctx: OracleContext,
        *,
        controller: Any,
        phase: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
        cmd = f"dumpsys package {self._package}"
        meta = _run_adb_shell(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)
        mentions_pkg = _stdout_mentions_package(stdout, self._package)

        artifact_rel = (
            Path("oracle")
            / "raw"
            / f"dumpsys_package_{_safe_name(self._package, default='pkg')}_appops_{phase}.txt"
        )
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)

        parsed: Dict[str, Any]
        try:
            parsed = parse_dumpsys_package_permissions(stdout, user_id=self._permission_user_id)
        except Exception as e:  # pragma: no cover
            parsed = {"ok": False, "errors": [f"parse_failed:{type(e).__name__}:{e}"]}

        snapshot = {
            "cmd": cmd,
            "dumpsys_ok": dumpsys_ok,
            "mentions_package": mentions_pkg,
            "meta": {k: v for k, v in meta.items() if k != "stdout"},
            "stdout_sha256": stable_sha256(stdout),
            "stdout_len": len(stdout),
            "artifact": artifact,
            "artifact_error": artifact_error,
            "parsed": parsed,
        }
        query = make_query(
            query_type="dumpsys",
            cmd=f"shell {cmd}",
            timeout_ms=self._timeout_ms,
            serial=ctx.serial,
            service="package",
            package=self._package,
        )
        return snapshot, query, artifact

    def _check_key(self, check: AppOpCheck) -> str:
        return f"{_normalize_op_name(check.op)}:{_normalize_scope(check.scope)}"

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
                            query_type="appops",
                            cmd=f"shell appops get {self._package}",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            package=self._package,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Baseline capture requires adb_shell to query appops state.",
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

        cmd = f"appops get {self._package}"
        meta = _run_adb_shell(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        ok = _adb_meta_ok(meta)
        error_kind = _appops_error_kind(stdout)
        mentions_pkg = _stdout_mentions_package(stdout, self._package)

        artifact_rel = (
            Path("oracle")
            / "raw"
            / f"appops_get_{_safe_name(self._package, default='pkg')}_pre.txt"
        )
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)
        artifacts = [artifact] if artifact is not None else None

        parsed = parse_appops_get_output(stdout)

        permission_snapshot = None
        permission_query = None
        permission_artifact = None
        if self._include_permission_snapshot:
            (
                permission_snapshot,
                permission_query,
                permission_artifact,
            ) = self._capture_permission_snapshot(ctx, controller=controller, phase="pre")
            if permission_artifact is not None:
                artifacts = (artifacts or []) + [permission_artifact]

        baseline: Dict[str, Dict[str, Any]] = {}
        for check in self._checks:
            mode, used_scope = _effective_op_mode(parsed, op=check.op, scope=check.scope)
            baseline[self._check_key(check)] = {
                "op": _normalize_op_name(check.op),
                "scope": _normalize_scope(check.scope),
                "mode": mode,
                "used_scope": used_scope,
            }
        self._baseline = baseline

        permission_ok = True
        if self._include_permission_snapshot and isinstance(permission_snapshot, dict):
            permission_ok = bool(permission_snapshot.get("dumpsys_ok", False)) and bool(
                permission_snapshot.get("mentions_package", False)
            )

        if not ok:
            conclusive = False
            success = False
            reason = f"appops get failed ({error_kind or 'unknown_error'})"
        elif self._include_permission_snapshot and not permission_ok:
            conclusive = False
            success = False
            reason = "permission snapshot unavailable (dumpsys package failed)"
        elif not mentions_pkg:
            conclusive = False
            success = False
            reason = "appops output did not mention expected package"
        elif not parsed.get("ok"):
            conclusive = False
            success = False
            reason = "failed to parse appops output"
        else:
            conclusive = True
            success = True
            reason = "baseline captured"

        queries = [
            make_query(
                query_type="appops",
                cmd=f"shell {cmd}",
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
                package=self._package,
            )
        ]
        if permission_query is not None:
            queries.append(permission_query)

        permission_preview: Dict[str, Any] | None = None
        if isinstance(permission_snapshot, dict):
            parsed_perm = permission_snapshot.get("parsed") or {}
            if isinstance(parsed_perm, dict):
                stats = parsed_perm.get("stats") or {}
                runtime_count = stats.get("runtime_count") if isinstance(stats, dict) else None
                install_count = stats.get("install_count") if isinstance(stats, dict) else None
                permission_preview = {
                    "dumpsys_ok": bool(permission_snapshot.get("dumpsys_ok", False)),
                    "parsed_ok": bool(parsed_perm.get("ok", False)),
                    "runtime_count": runtime_count,
                    "install_count": install_count,
                }

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="pre",
                queries=queries,
                result_for_digest={
                    "package": self._package,
                    "ok": ok,
                    "error_kind": error_kind,
                    "mentions_package": mentions_pkg,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "checks": [c.__dict__ for c in self._checks],
                    "parsed": parsed,
                    "baseline": baseline,
                    "permission_snapshot": permission_snapshot,
                },
                result_preview={
                    "package": self._package,
                    "baseline": {k: v.get("mode") for k, v in baseline.items()},
                    "permission_snapshot": permission_preview,
                },
                anti_gaming_notes=[
                    (
                        "Pollution control: captures baseline appops state in pre_check so "
                        "post_check can require an in-episode transition."
                    ),
                    "Hard evidence: reads AppOps state via adb shell (UI spoof-resistant).",
                    "Anti-gaming: records a permission snapshot via `dumpsys package`.",
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
                            query_type="appops",
                            cmd=f"shell appops get {self._package}",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            package=self._package,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: queries AppOps state via adb shell.",
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
                            query_type="appops",
                            cmd=f"shell appops get {self._package}",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            package=self._package,
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        "Hard oracle: requires an episode time anchor for time-window binding.",
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
                        "Hard oracle: needs device epoch time for episode window binding.",
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

        cmd = f"appops get {self._package}"
        meta = _run_adb_shell(controller, cmd=cmd, timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        ok = _adb_meta_ok(meta)
        error_kind = _appops_error_kind(stdout)
        mentions_pkg = _stdout_mentions_package(stdout, self._package)

        artifact_rel = (
            Path("oracle")
            / "raw"
            / f"appops_get_{_safe_name(self._package, default='pkg')}_post.txt"
        )
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)
        artifacts = [artifact] if artifact is not None else None

        parsed = parse_appops_get_output(stdout)

        permission_snapshot = None
        permission_query = None
        permission_artifact = None
        if self._include_permission_snapshot:
            (
                permission_snapshot,
                permission_query,
                permission_artifact,
            ) = self._capture_permission_snapshot(ctx, controller=controller, phase="post")
            if permission_artifact is not None:
                artifacts = (artifacts or []) + [permission_artifact]

        per_check: List[Dict[str, Any]] = []
        failures: List[str] = []
        inconclusive_reasons: List[str] = []

        for check in self._checks:
            key = self._check_key(check)
            current_mode, used_scope = _effective_op_mode(parsed, op=check.op, scope=check.scope)
            baseline_mode = (self._baseline.get(key) or {}).get("mode")

            expected = tuple(_normalize_mode(v) for v in check.expected_any_of)
            matched_state = current_mode is not None and _normalize_mode(current_mode) in expected

            changed_ok: Optional[bool] = None
            if check.require_change_in_window:
                if baseline_mode is None:
                    changed_ok = None
                else:
                    baseline_norm = _normalize_mode(baseline_mode)
                    changed_ok = (baseline_norm not in expected) and matched_state

            check_ok: Optional[bool]
            if current_mode is None:
                check_ok = None
            elif not matched_state:
                check_ok = False
            elif check.require_change_in_window and changed_ok is not True:
                check_ok = None if changed_ok is None else False
            else:
                check_ok = True

            per_check.append(
                {
                    "op": _normalize_op_name(check.op),
                    "scope": _normalize_scope(check.scope),
                    "expected_any_of": expected,
                    "require_change_in_window": check.require_change_in_window,
                    "baseline_mode": baseline_mode,
                    "current_mode": current_mode,
                    "used_scope": used_scope,
                    "matched_state": matched_state,
                    "changed_ok": changed_ok,
                    "ok": check_ok,
                }
            )

            if check_ok is True:
                continue
            if check_ok is False:
                if current_mode is None:
                    failures.append(f"{check.op}: missing current mode")
                elif not matched_state:
                    failures.append(f"{check.op}: mode {current_mode!r} not in {expected}")
                else:
                    failures.append(f"{check.op}: did not change within episode window")
            else:
                if current_mode is None:
                    inconclusive_reasons.append(f"{check.op}: could not determine current mode")
                else:
                    inconclusive_reasons.append(
                        f"{check.op}: missing baseline (pre_check) for time window binding"
                    )

        op_modes = {f"{row['op']}:{row['scope']}": row["current_mode"] for row in per_check}

        permission_ok = True
        if self._include_permission_snapshot and isinstance(permission_snapshot, dict):
            permission_ok = bool(permission_snapshot.get("dumpsys_ok", False)) and bool(
                permission_snapshot.get("mentions_package", False)
            )

        if not ok:
            conclusive = False
            success = False
            reason = f"appops get failed ({error_kind or 'unknown_error'})"
        elif self._include_permission_snapshot and not permission_ok:
            conclusive = False
            success = False
            reason = "permission snapshot unavailable (dumpsys package failed)"
        elif not mentions_pkg:
            conclusive = False
            success = False
            reason = "appops output did not mention expected package"
        elif not parsed.get("ok"):
            conclusive = False
            success = False
            reason = "failed to parse appops output"
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
            reason = "all appops checks matched"

        queries = [
            make_query(
                query_type="appops",
                cmd=f"shell {cmd}",
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
                package=self._package,
            )
        ]
        if permission_query is not None:
            queries.append(permission_query)

        permission_preview: Dict[str, Any] | None = None
        if isinstance(permission_snapshot, dict):
            parsed_perm = permission_snapshot.get("parsed") or {}
            if isinstance(parsed_perm, dict):
                stats = parsed_perm.get("stats") or {}
                runtime_count = stats.get("runtime_count") if isinstance(stats, dict) else None
                install_count = stats.get("install_count") if isinstance(stats, dict) else None
                permission_preview = {
                    "dumpsys_ok": bool(permission_snapshot.get("dumpsys_ok", False)),
                    "parsed_ok": bool(parsed_perm.get("ok", False)),
                    "runtime_count": runtime_count,
                    "install_count": install_count,
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
                    "window": window.__dict__,
                    "window_meta": window_meta,
                    "ok": ok,
                    "error_kind": error_kind,
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
                    "op_modes": op_modes,
                    "permission_snapshot": permission_snapshot,
                },
                result_preview={
                    "package": self._package,
                    "op_modes": op_modes,
                    "failure": failures[0] if failures else None,
                    "inconclusive": inconclusive_reasons[0] if inconclusive_reasons else None,
                    "permission_snapshot": permission_preview,
                },
                anti_gaming_notes=[
                    "Hard evidence: reads AppOps state via adb shell (UI spoof-resistant).",
                    (
                        "Anti-gaming: binds to package identity and enforces an episode time "
                        "window by requiring a pre_check baseline transition (default)."
                    ),
                    (
                        "Evidence hygiene: stores raw appops output as an artifact and records "
                        "structured fields + digests in oracle_trace."
                    ),
                    "Anti-gaming: records a permission snapshot via `dumpsys package`.",
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


@register_oracle(AppOpsOracle.oracle_id)
def _make_appops(cfg: Mapping[str, Any]) -> Oracle:
    package = cfg.get("package") or cfg.get("pkg")
    if not isinstance(package, str) or not package.strip():
        raise ValueError("AppOpsOracle requires 'package' string")

    checks = _parse_appop_checks(cfg)
    timeout_ms = int(cfg.get("timeout_ms", 8000))
    include_permission_snapshot = _coerce_bool(
        cfg.get("include_permission_snapshot", True), default=True
    )
    permission_user_id = int(cfg.get("permission_user_id", cfg.get("user_id", 0)))
    return AppOpsOracle(
        package=package.strip(),
        checks=checks,
        include_permission_snapshot=include_permission_snapshot,
        permission_user_id=permission_user_id,
        timeout_ms=timeout_ms,
    )


@register_oracle("AppOpsOracle")
def _make_appops_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_appops(cfg)
