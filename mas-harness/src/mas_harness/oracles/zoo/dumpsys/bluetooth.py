"""Bluetooth state oracle.

Phase 2 Step 7.2 (vertical): Connectivity/Location/Bluetooth Oracles.

This oracle reads Bluetooth state via:
  - `settings get global bluetooth_on`
  - `dumpsys bluetooth_manager` (best-effort cross-check)

If the requested signal cannot be read/parsed reliably, the oracle returns
`conclusive=false` (capability-gated / version-tolerant).
"""

from __future__ import annotations

import re
import shlex
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

_LOOKS_LIKE_BLUETOOTH_RE = re.compile(
    r"\bBluetooth\b|\bdumpsys\s+bluetooth_manager\b|\bBluetoothManagerService\b",
    flags=re.IGNORECASE,
)

_ENABLED_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\benabled:\s*(?P<val>true|false)\b", flags=re.IGNORECASE),
    re.compile(r"\bmEnabled\s*=\s*(?P<val>true|false)\b", flags=re.IGNORECASE),
    re.compile(r"\bBluetooth is\s+(?P<state>enabled|disabled)\b", flags=re.IGNORECASE),
)

_BOOL_TRUE = {"1", "true", "on", "enabled", "yes"}
_BOOL_FALSE = {"0", "false", "off", "disabled", "no"}


def _shell_cmd(*parts: str) -> str:
    return " ".join(shlex.quote(str(p)) for p in parts)


def _settings_get_cmd(*, namespace: str, key: str) -> str:
    return _shell_cmd("settings", "get", namespace, key)


def _parse_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        return None
    text = str(value).strip().lower()
    if not text or text == "null":
        return None
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    return None


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
    text = "\n".join([str(meta.get("stdout", "") or ""), str(meta.get("stderr", "") or "")]).lower()
    if "permission denial" in text or "securityexception" in text:
        return False
    if text.strip().startswith("error:"):
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


def parse_bluetooth_enabled(text: str) -> Dict[str, Any]:
    stdout = str(text or "").replace("\r", "")
    if not stdout.strip():
        return {"ok": False, "enabled": None, "errors": ["empty dumpsys output"]}

    if not _LOOKS_LIKE_BLUETOOTH_RE.search(stdout):
        return {
            "ok": False,
            "enabled": None,
            "errors": ["output does not look like dumpsys bluetooth_manager"],
        }

    for pat in _ENABLED_PATTERNS:
        m = pat.search(stdout)
        if not m:
            continue
        if "val" in m.groupdict():
            raw = (m.group("val") or "").strip().lower()
            if raw in {"true", "false"}:
                return {"ok": True, "enabled": raw == "true", "errors": []}
        if "state" in m.groupdict():
            state = (m.group("state") or "").strip().lower()
            if state in {"enabled", "disabled"}:
                return {"ok": True, "enabled": state == "enabled", "errors": []}

    return {
        "ok": False,
        "enabled": None,
        "errors": ["failed to find bluetooth enabled state in dumpsys output"],
    }


class BluetoothOracle(Oracle):
    """Hard oracle: validate Bluetooth enabled state via settings + dumpsys."""

    oracle_id = "bluetooth"
    oracle_name = "bluetooth"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        enabled: Any,
        timeout_ms: int = 10_000,
    ) -> None:
        expected_enabled = _parse_bool(enabled)
        if expected_enabled is None:
            raise ValueError("BluetoothOracle.enabled must be bool-like (0/1/true/false)")
        self._expected_enabled = expected_enabled
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
                            query_type="settings",
                            op="get",
                            namespace="global",
                            key="bluetooth_on",
                            cmd="shell settings get global bluetooth_on",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: reads bluetooth state via adb shell commands "
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

        setting_meta = _run_adb_shell(
            controller,
            cmd=_settings_get_cmd(namespace="global", key="bluetooth_on"),
            timeout_ms=self._timeout_ms,
        )
        dumpsys_meta = _run_adb_shell(
            controller, cmd="dumpsys bluetooth_manager", timeout_ms=self._timeout_ms
        )

        queries.append(
            make_query(
                query_type="settings",
                op="get",
                namespace="global",
                key="bluetooth_on",
                cmd="shell settings get global bluetooth_on",
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
            )
        )
        queries.append(
            make_query(
                query_type="dumpsys",
                cmd="shell dumpsys bluetooth_manager",
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
                service="bluetooth_manager",
            )
        )

        settings_enabled = _parse_bool(
            setting_meta.get("stdout") if _adb_meta_ok(setting_meta) else None
        )

        dumpsys_stdout = (
            str(dumpsys_meta.get("stdout", "") or "") if _adb_meta_ok(dumpsys_meta) else ""
        )
        dumpsys_ok = _adb_meta_ok(dumpsys_meta)
        parsed = (
            parse_bluetooth_enabled(dumpsys_stdout)
            if dumpsys_ok
            else {"ok": False, "enabled": None, "errors": ["dumpsys failed"]}
        )
        dumpsys_enabled = parsed.get("enabled") if isinstance(parsed, MappingABC) else None

        artifact_rel = Path("oracle") / "raw" / "dumpsys_bluetooth_manager_post.txt"
        artifact, artifact_error = _write_text_artifact(
            ctx, rel_path=artifact_rel, text=dumpsys_stdout
        )

        inconsistent = (
            isinstance(settings_enabled, bool)
            and isinstance(dumpsys_enabled, bool)
            and settings_enabled != dumpsys_enabled
        )

        observed_enabled = (
            settings_enabled if isinstance(settings_enabled, bool) else dumpsys_enabled
        )

        missing: List[str] = []
        mismatches: List[str] = []

        if inconsistent:
            missing.append("inconsistent_sources")

        if not isinstance(observed_enabled, bool):
            missing.append("enabled")
        elif observed_enabled != self._expected_enabled:
            mismatches.append("enabled")

        if missing:
            conclusive = False
            success = False
            reason = f"missing/unknown bluetooth field(s): {', '.join(sorted(missing))}"
        else:
            conclusive = True
            success = not mismatches
            reason = (
                "bluetooth state matched expected value"
                if success
                else "bluetooth enabled state mismatch"
            )

        artifacts = [artifact] if artifact is not None else None

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=queries,
                result_for_digest={
                    "expected": {"enabled": self._expected_enabled},
                    "observed": {
                        "enabled_settings": settings_enabled,
                        "enabled_dumpsys": dumpsys_enabled,
                        "enabled": observed_enabled,
                        "inconsistent": inconsistent,
                    },
                    "settings_ok": _adb_meta_ok(setting_meta),
                    "dumpsys_ok": dumpsys_ok,
                    "dumpsys_meta": {k: v for k, v in dumpsys_meta.items() if k != "stdout"},
                    "dumpsys_stdout_sha256": stable_sha256(dumpsys_stdout),
                    "dumpsys_stdout_len": len(dumpsys_stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "parsed": parsed,
                    "missing": sorted(missing),
                    "mismatches": sorted(mismatches),
                },
                result_preview={
                    "success": success,
                    "missing": sorted(missing),
                    "mismatches": sorted(mismatches),
                    "observed": {"enabled": observed_enabled, "inconsistent": inconsistent},
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: reads Bluetooth toggle via adb `settings get` and "
                        "cross-checks via `dumpsys bluetooth_manager` (UI spoof-resistant)."
                    ),
                    (
                        "Capability-gated: returns conclusive=false when the target signal "
                        "cannot be read/parsed reliably."
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


@register_oracle(BluetoothOracle.oracle_id)
def _make_bluetooth_oracle(cfg: Mapping[str, Any]) -> Oracle:
    expected = cfg.get("expected") if isinstance(cfg.get("expected"), MappingABC) else {}
    enabled = expected.get("enabled", cfg.get("enabled"))
    return BluetoothOracle(enabled=enabled, timeout_ms=int(cfg.get("timeout_ms", 10_000)))


@register_oracle("BluetoothOracle")
def _make_bluetooth_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_bluetooth_oracle(cfg)
