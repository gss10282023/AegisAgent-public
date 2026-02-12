"""Settings / system-state hard oracles (Phase 2 Step 9).

Implements:
- SettingsOracle: validate `adb shell settings get` results (optionally use
  `settings put` in pre_check to enforce a baseline state).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
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

_ALLOWED_NAMESPACES = {"system", "secure", "global"}


@dataclass(frozen=True)
class SettingCheck:
    namespace: str
    key: str
    expected_any_of: Tuple[str, ...]
    pre_value: Optional[str] = None


def _normalize_namespace(ns: Any) -> str:
    val = str(ns or "").strip().lower()
    if val not in _ALLOWED_NAMESPACES:
        raise ValueError(f"settings namespace must be one of {_ALLOWED_NAMESPACES}, got: {ns!r}")
    return val


def _shell_cmd(*parts: str) -> str:
    return " ".join(shlex.quote(str(p)) for p in parts)


def _settings_get_cmd(*, namespace: str, key: str) -> str:
    return _shell_cmd("settings", "get", namespace, key)


def _settings_put_cmd(*, namespace: str, key: str, value: str) -> str:
    return _shell_cmd("settings", "put", namespace, key, value)


def _run_adb_shell(controller: Any, *, cmd: str, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"cmd": cmd, "timeout_ms": int(timeout_ms)}
    if not hasattr(controller, "adb_shell"):
        meta["error"] = "missing controller capability: adb_shell"
        return meta

    try:
        try:
            res = controller.adb_shell(cmd, timeout_ms=timeout_ms, check=False)
        except TypeError:
            res = controller.adb_shell(cmd, timeout_s=float(timeout_ms) / 1000.0, check=False)
    except TypeError:
        # Toy/fake controllers may not accept timeout parameters.
        res = controller.adb_shell(cmd)
    except Exception as e:  # pragma: no cover
        meta["error"] = repr(e)
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

    meta.update({"args": None, "returncode": 0, "stderr": None, "stdout": str(res)})
    return meta


def _adb_meta_ok(meta: Mapping[str, Any]) -> bool:
    if meta.get("error"):
        return False
    rc = meta.get("returncode")
    if isinstance(rc, int) and rc != 0:
        return False

    text = "\n".join(
        [
            str(meta.get("stdout", "") or ""),
            str(meta.get("stderr", "") or ""),
        ]
    ).lower()
    if "permission denial" in text or "securityexception" in text:
        return False
    if text.strip().startswith("error:"):
        return False
    return True


def _parse_setting_checks(cfg: Mapping[str, Any]) -> List[SettingCheck]:
    raw = cfg.get("checks") or cfg.get("settings")
    if raw is None:
        ns = cfg.get("namespace") or cfg.get("ns")
        key = cfg.get("key")
        if ns is not None or key is not None:
            raw = [cfg]

    if not isinstance(raw, list) or not raw:
        raise ValueError("SettingsOracle requires a non-empty list 'checks' (or 'settings').")

    checks: List[SettingCheck] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("SettingsOracle.checks items must be objects")
        namespace = _normalize_namespace(item.get("namespace") or item.get("ns"))
        key = str(item.get("key") or "").strip()
        if not key:
            raise ValueError("SettingsOracle.checks[].key must be a non-empty string")

        expected = item.get("expected")
        if expected is None:
            expected = item.get("expect")
        if expected is None:
            expected = item.get("value")
        if expected is None:
            raise ValueError("SettingsOracle.checks[] requires 'expected' value")

        if isinstance(expected, (list, tuple, set)):
            expected_values = tuple(str(v) for v in expected)
        else:
            expected_values = (str(expected),)
        expected_values = tuple(v.strip() for v in expected_values if str(v).strip())
        if not expected_values:
            raise ValueError("SettingsOracle.checks[].expected must not be empty")

        pre_value = item.get("pre_value")
        if pre_value is None:
            pre_value = item.get("pre")
        if pre_value is None:
            pre_value = item.get("baseline")
        pre_value_str = str(pre_value) if pre_value is not None else None

        checks.append(
            SettingCheck(
                namespace=namespace,
                key=key,
                expected_any_of=expected_values,
                pre_value=pre_value_str,
            )
        )
    return checks


class SettingsOracle(Oracle):
    oracle_id = "settings"
    oracle_name = "settings"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(self, *, checks: Sequence[SettingCheck], timeout_ms: int = 1500) -> None:
        self._checks = list(checks)
        if not self._checks:
            raise ValueError("SettingsOracle requires at least one check")
        self._timeout_ms = int(timeout_ms)
        self._pre_values: Dict[Tuple[str, str], Optional[str]] = {}

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller
        if not hasattr(controller, "adb_shell"):
            return []

        if not any(c.pre_value is not None for c in self._checks):
            return []

        queries: List[Dict[str, Any]] = []
        results: List[Dict[str, Any]] = []
        all_put_ok = True

        for check in self._checks:
            before_cmd = _settings_get_cmd(namespace=check.namespace, key=check.key)
            before_meta = _run_adb_shell(controller, cmd=before_cmd, timeout_ms=self._timeout_ms)
            before_val = (
                str(before_meta.get("stdout", "") or "").strip()
                if _adb_meta_ok(before_meta)
                else None
            )
            self._pre_values[(check.namespace, check.key)] = before_val

            queries.append(
                make_query(
                    query_type="settings",
                    op="get",
                    namespace=check.namespace,
                    key=check.key,
                    cmd=f"shell {before_cmd}",
                    timeout_ms=self._timeout_ms,
                    serial=ctx.serial,
                )
            )

            put_meta = None
            after_meta = None
            after_val = None
            if check.pre_value is not None:
                put_cmd = _settings_put_cmd(
                    namespace=check.namespace, key=check.key, value=str(check.pre_value)
                )
                put_meta = _run_adb_shell(controller, cmd=put_cmd, timeout_ms=self._timeout_ms)
                all_put_ok = all_put_ok and _adb_meta_ok(put_meta)
                queries.append(
                    make_query(
                        query_type="settings",
                        op="put",
                        namespace=check.namespace,
                        key=check.key,
                        cmd=f"shell {put_cmd}",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                    )
                )

                after_cmd = _settings_get_cmd(namespace=check.namespace, key=check.key)
                after_meta = _run_adb_shell(controller, cmd=after_cmd, timeout_ms=self._timeout_ms)
                after_val = (
                    str(after_meta.get("stdout", "") or "").strip()
                    if _adb_meta_ok(after_meta)
                    else None
                )
                queries.append(
                    make_query(
                        query_type="settings",
                        op="get",
                        namespace=check.namespace,
                        key=check.key,
                        cmd=f"shell {after_cmd}",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                    )
                )

            results.append(
                {
                    "namespace": check.namespace,
                    "key": check.key,
                    "pre_value_expected": check.pre_value,
                    "before": {
                        "ok": _adb_meta_ok(before_meta),
                        "value": before_val,
                        "meta": before_meta,
                    },
                    "put": {
                        "ok": _adb_meta_ok(put_meta) if isinstance(put_meta, dict) else None,
                        "meta": put_meta,
                    },
                    "after": {
                        "ok": _adb_meta_ok(after_meta) if isinstance(after_meta, dict) else None,
                        "value": after_val,
                        "meta": after_meta,
                    },
                }
            )

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="pre",
                queries=queries,
                result_for_digest={
                    "checks": [c.__dict__ for c in self._checks],
                    "results": results,
                },
                result_preview={
                    "checks": [
                        {
                            "namespace": r["namespace"],
                            "key": r["key"],
                            "before": r["before"]["value"],
                            "after": r["after"]["value"],
                        }
                        for r in results
                    ],
                    "all_put_ok": all_put_ok,
                },
                anti_gaming_notes=[
                    (
                        "Pollution control: optionally sets a known baseline via adb "
                        "`settings put` so post_check can meaningfully validate changes."
                    ),
                    "Hard evidence: captures both baseline and post-run values via `settings get`.",
                ],
                decision=make_decision(
                    success=all_put_ok,
                    score=1.0 if all_put_ok else 0.0,
                    reason="baseline settings applied"
                    if all_put_ok
                    else "failed to apply baseline settings",
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
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
                            query_type="settings",
                            op="get",
                            namespace="global",
                            key="...",
                            cmd="shell settings get <namespace> <key>",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: validates device settings via adb `settings get` "
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

        queries: List[Dict[str, Any]] = []
        per_check: List[Dict[str, Any]] = []
        mismatches: List[Dict[str, Any]] = []
        all_get_ok = True

        for check in self._checks:
            get_cmd = _settings_get_cmd(namespace=check.namespace, key=check.key)
            meta = _run_adb_shell(controller, cmd=get_cmd, timeout_ms=self._timeout_ms)
            ok = _adb_meta_ok(meta)
            all_get_ok = all_get_ok and ok

            val = str(meta.get("stdout", "") or "").strip() if ok else None
            expected = tuple(v.strip() for v in check.expected_any_of)
            matched = val is not None and val.strip() in expected

            baseline = self._pre_values.get((check.namespace, check.key))

            per_check.append(
                {
                    "namespace": check.namespace,
                    "key": check.key,
                    "expected_any_of": expected,
                    "actual": val,
                    "matched": matched,
                    "ok": ok,
                    "baseline_pre": baseline,
                    "meta": meta,
                }
            )

            if ok and not matched:
                mismatches.append(
                    {
                        "namespace": check.namespace,
                        "key": check.key,
                        "expected_any_of": expected,
                        "actual": val,
                    }
                )

            queries.append(
                make_query(
                    query_type="settings",
                    op="get",
                    namespace=check.namespace,
                    key=check.key,
                    cmd=f"shell {get_cmd}",
                    timeout_ms=self._timeout_ms,
                    serial=ctx.serial,
                )
            )

        if not all_get_ok:
            conclusive = False
            success = False
            reason = "failed to query one or more settings"
        elif mismatches:
            conclusive = True
            success = False
            reason = f"{len(mismatches)} setting(s) did not match expected value"
        else:
            conclusive = True
            success = True
            reason = "all settings matched expected values"

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=queries,
                result_for_digest={
                    "checks": [c.__dict__ for c in self._checks],
                    "per_check": per_check,
                    "mismatches": mismatches,
                },
                result_preview={
                    "success": success,
                    "mismatch_count": len(mismatches),
                    "mismatches": mismatches[:5],
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: validates device settings via adb `settings get`, "
                        "robust to UI-only spoofing."
                    ),
                    "Anti-gaming: exact match on explicit namespace+key and expected value(s).",
                    (
                        "Optional pollution control: use pre_check baseline (settings put) so a "
                        "task cannot pass from pre-existing state."
                    ),
                ],
                decision=make_decision(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]


@register_oracle(SettingsOracle.oracle_id)
def _make_settings(cfg: Mapping[str, Any]) -> Oracle:
    timeout_ms = int(cfg.get("timeout_ms", 1500))
    checks = _parse_setting_checks(cfg)
    return SettingsOracle(checks=checks, timeout_ms=timeout_ms)


@register_oracle("SettingsOracle")
def _make_settings_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_settings(cfg)
