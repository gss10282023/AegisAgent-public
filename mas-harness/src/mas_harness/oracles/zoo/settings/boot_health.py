"""Boot health / infra probes (Phase 2 Step 6).

This module serves two purposes:
- Provide a small "boot_health" oracle plugin for Oracle Zoo.
- Provide reusable best-effort probes whose results are written into
  `device_trace.jsonl` and used for run-level failure attribution.
"""

from __future__ import annotations

import re
import shlex
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


def _run_adb_shell_best_effort(
    controller: Any, *, cmd: str, timeout_ms: int
) -> Tuple[Optional[str], Dict[str, Any]]:
    meta: Dict[str, Any] = {"cmd": cmd, "timeout_ms": int(timeout_ms)}
    if not hasattr(controller, "adb_shell"):
        meta["error"] = "missing controller capability: adb_shell"
        return None, meta

    try:
        try:
            res = controller.adb_shell(cmd, timeout_ms=timeout_ms, check=False)
        except TypeError:
            res = controller.adb_shell(cmd, timeout_s=float(timeout_ms) / 1000.0, check=False)
    except TypeError:
        try:
            res = controller.adb_shell(cmd)
        except Exception as e:  # pragma: no cover
            meta["error"] = repr(e)
            return None, meta
    except Exception as e:  # pragma: no cover
        meta["error"] = repr(e)
        return None, meta

    if hasattr(res, "stdout") or hasattr(res, "returncode"):
        stdout = str(getattr(res, "stdout", "") or "")
        meta.update(
            {
                "args": getattr(res, "args", None),
                "returncode": getattr(res, "returncode", None),
                "stderr": getattr(res, "stderr", None),
                "stdout": stdout,
            }
        )
        ok = getattr(res, "returncode", 0) == 0
        meta["ok"] = bool(ok)
        return stdout, meta

    # Toy/fake controllers may return raw stdout strings.
    stdout = str(res)
    meta.update({"stdout": stdout, "ok": True})
    return stdout, meta


_BOOL_TRUE = {"1", "true", "on", "enabled", "yes"}
_BOOL_FALSE = {"0", "false", "off", "disabled", "no"}


def _parse_boolish(text: str) -> Optional[bool]:
    val = str(text or "").strip().lower()
    if val in _BOOL_TRUE:
        return True
    if val in _BOOL_FALSE:
        return False
    return None


def _truncate_stdout_in_meta(meta: Dict[str, Any], *, limit: int = 8192) -> None:
    stdout = meta.get("stdout")
    if not isinstance(stdout, str):
        return
    if len(stdout) <= limit:
        return
    meta["stdout_len"] = len(stdout)
    meta["stdout_truncated"] = True
    meta["stdout"] = stdout[:limit] + "\n...<truncated>"


def probe_adb_shell_ok(
    controller: Any, *, timeout_ms: int = 1500
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """Best-effort probe that adb shell is responsive."""

    stdout, meta = _run_adb_shell_best_effort(
        controller,
        cmd="echo __mas_adb_ok__",
        timeout_ms=timeout_ms,
    )
    if meta.get("error") == "missing controller capability: adb_shell":
        return None, meta
    if meta.get("ok") is False or "error" in meta:
        return False, meta
    if stdout is None:
        return False, meta
    return True, meta


def probe_boot_completed(
    controller: Any, *, timeout_ms: int = 1500
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """Best-effort probe of Android boot completion."""

    stdout, meta = _run_adb_shell_best_effort(
        controller, cmd="getprop sys.boot_completed", timeout_ms=timeout_ms
    )
    if meta.get("error") == "missing controller capability: adb_shell":
        return None, meta
    if meta.get("ok") is False or "error" in meta:
        return None, meta
    val = (stdout or "").strip()
    return val == "1", meta


def probe_sdcard_writable(
    controller: Any,
    *,
    timeout_ms: int = 2000,
    probe_path: str = "/sdcard/mas_probe_sdcard_writable.txt",
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """Best-effort probe that /sdcard is writable."""

    write_cmd = f"sh -c {shlex.quote(f'echo ok > {probe_path}')}"
    _, write_meta = _run_adb_shell_best_effort(controller, cmd=write_cmd, timeout_ms=timeout_ms)

    # Best-effort cleanup (do not mask write result).
    rm_cmd = f"rm -f {shlex.quote(probe_path)}"
    _, rm_meta = _run_adb_shell_best_effort(controller, cmd=rm_cmd, timeout_ms=timeout_ms)

    meta = {"write": write_meta, "cleanup": rm_meta, "probe_path": probe_path}

    if write_meta.get("error") == "missing controller capability: adb_shell":
        return None, meta
    if write_meta.get("ok") is False or "error" in write_meta:
        return False, meta
    return True, meta


def probe_airplane_mode_on(
    controller: Any, *, timeout_ms: int = 1500
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """Best-effort probe of airplane mode state."""

    stdout, meta = _run_adb_shell_best_effort(
        controller, cmd="settings get global airplane_mode_on", timeout_ms=timeout_ms
    )
    if meta.get("error") == "missing controller capability: adb_shell":
        return None, meta
    if meta.get("ok") is False or "error" in meta:
        return None, meta

    parsed = _parse_boolish(stdout or "")
    if parsed is None:
        meta["parse_failed"] = True
    return parsed, meta


def probe_auto_time_enabled(
    controller: Any, *, timeout_ms: int = 1500
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """Best-effort probe of whether device uses automatic time."""

    stdout, meta = _run_adb_shell_best_effort(
        controller, cmd="settings get global auto_time", timeout_ms=timeout_ms
    )
    if meta.get("error") == "missing controller capability: adb_shell":
        return None, meta
    if meta.get("ok") is False or "error" in meta:
        return None, meta

    parsed = _parse_boolish(stdout or "")
    if parsed is None:
        meta["parse_failed"] = True
    return parsed, meta


def probe_device_timezone(
    controller: Any, *, timeout_ms: int = 1500
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Best-effort probe of device timezone (IANA tz database name)."""

    stdout, meta = _run_adb_shell_best_effort(
        controller, cmd="getprop persist.sys.timezone", timeout_ms=timeout_ms
    )
    if meta.get("error") == "missing controller capability: adb_shell":
        return None, meta
    if meta.get("ok") is False or "error" in meta:
        return None, meta

    val = str(stdout or "").strip()
    if not val or val.lower() == "null":
        return None, meta
    return val, meta


_ACTIVE_NET_RE = re.compile(
    r"(?:Active default network|mActiveDefaultNetwork|Default network):\s*(\S+)",
    re.IGNORECASE,
)
_NETID_RE = re.compile(r"-?\d+")
_NAI_HEADER_RE = re.compile(r"NetworkAgentInfo\s*\[([^\]]+)\]", re.IGNORECASE)


def _parse_active_network_from_connectivity_dumpsys(
    text: str,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Parse a minimal active-network summary from `dumpsys connectivity` output."""

    parsed_meta: Dict[str, Any] = {}

    m = _ACTIVE_NET_RE.search(text or "")
    if not m:
        parsed_meta["parse_failed"] = True
        parsed_meta["reason"] = "active_default_network_not_found"
        return None, parsed_meta

    raw = m.group(1)
    raw_lower = raw.strip().lower()
    if raw_lower in {"null", "none"}:
        return (
            {"net_id": None, "transport": None, "name": None, "connected": None, "validated": None},
            {"raw_active_default_network": raw},
        )

    netid_match = _NETID_RE.search(raw)
    if not netid_match:
        parsed_meta["parse_failed"] = True
        parsed_meta["reason"] = "active_default_network_unparseable"
        parsed_meta["raw_active_default_network"] = raw
        return None, parsed_meta

    net_id = int(netid_match.group(0))
    if net_id < 0:
        return (
            {"net_id": None, "transport": None, "name": None, "connected": None, "validated": None},
            {"raw_active_default_network": raw},
        )

    lines = str(text or "").splitlines()
    header_idx = None
    header_inside = None
    for idx, line in enumerate(lines):
        m2 = _NAI_HEADER_RE.search(line)
        if not m2:
            continue
        inside = m2.group(1)
        if re.search(rf"-\s*{net_id}\b", inside):
            header_idx = idx
            header_inside = inside
            break

    name = None
    transport = None
    if header_inside:
        name = header_inside.split()[0].strip() if header_inside.split() else None
        token = (name or "").upper()
        if token in {"WIFI", "WI-FI"}:
            transport = "wifi"
        elif token in {"MOBILE", "CELLULAR"}:
            transport = "cellular"
        elif token == "ETHERNET":
            transport = "ethernet"
        elif token == "VPN":
            transport = "vpn"
        elif token == "BLUETOOTH":
            transport = "bluetooth"
        elif token:
            transport = token.lower()

    block_text = None
    if header_idx is not None:
        end_idx = len(lines)
        for j in range(header_idx + 1, len(lines)):
            if "NetworkAgentInfo" in lines[j]:
                end_idx = j
                break
        block_text = "\n".join(lines[header_idx:end_idx])

    connected: Optional[bool] = None
    validated: Optional[bool] = None
    if block_text:
        upper = block_text.upper()
        if re.search(r"\bCONNECTED\b", upper):
            connected = True
        if "VALIDATED" in upper:
            validated = True

    return (
        {
            "net_id": net_id,
            "transport": transport,
            "name": name,
            "connected": connected,
            "validated": validated,
        },
        {"raw_active_default_network": raw},
    )


def probe_active_network(
    controller: Any, *, timeout_ms: int = 2500
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Best-effort probe of active network via dumpsys connectivity."""

    stdout, meta = _run_adb_shell_best_effort(
        controller, cmd="dumpsys connectivity", timeout_ms=timeout_ms
    )
    if meta.get("error") == "missing controller capability: adb_shell":
        return None, meta
    if meta.get("ok") is False or "error" in meta:
        return None, meta

    parsed, parsed_meta = _parse_active_network_from_connectivity_dumpsys(stdout or "")
    meta["parsed"] = parsed_meta
    _truncate_stdout_in_meta(meta)
    return parsed, meta


def capture_device_infra(
    controller: Any,
    *,
    device_epoch_time_ms: Optional[int] = None,
    timeout_ms: int = 1500,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Capture infra health signals for device_trace.jsonl.

    Returns (event, analysis) where analysis is suitable for failure attribution.
    """

    adb_ok, adb_meta = probe_adb_shell_ok(controller, timeout_ms=timeout_ms)
    boot_completed, boot_meta = probe_boot_completed(controller, timeout_ms=timeout_ms)
    sdcard_writable, sd_meta = probe_sdcard_writable(controller, timeout_ms=max(2000, timeout_ms))
    airplane_mode_on, airplane_meta = probe_airplane_mode_on(controller, timeout_ms=timeout_ms)
    active_network, network_meta = probe_active_network(
        controller, timeout_ms=max(2500, timeout_ms)
    )
    auto_time, auto_time_meta = probe_auto_time_enabled(controller, timeout_ms=timeout_ms)
    timezone, tz_meta = probe_device_timezone(controller, timeout_ms=timeout_ms)

    event = {
        "event": "infra_probe",
        "adb_shell_ok": adb_ok,
        "adb_shell_probe": adb_meta,
        "airplane_mode_on": airplane_mode_on,
        "airplane_mode_on_probe": airplane_meta,
        "network": active_network,
        "network_probe": network_meta,
        "auto_time": auto_time,
        "auto_time_probe": auto_time_meta,
        "device_timezone": timezone,
        "device_timezone_probe": tz_meta,
        "boot_completed": boot_completed,
        "boot_completed_probe": boot_meta,
        "device_epoch_time_ms": device_epoch_time_ms,
        "sdcard_writable": sdcard_writable,
        "sdcard_writable_probe": sd_meta,
    }

    infra_failed, reasons = infer_infra_failure(event)
    analysis = {"infra_failed": infra_failed, "infra_failure_reasons": reasons}
    return event, analysis


def infer_infra_failure(infra_event: Mapping[str, Any]) -> Tuple[bool, list[str]]:
    """Apply Step-6 fixed attribution rules to an infra probe event."""

    reasons: list[str] = []

    if infra_event.get("adb_shell_ok") is False:
        reasons.append("adb_shell_unavailable")
    if infra_event.get("boot_completed") is False:
        reasons.append("boot_not_completed")
    if infra_event.get("sdcard_writable") is False:
        reasons.append("sdcard_not_writable")

    return bool(reasons), reasons


class BootHealthOracle(Oracle):
    oracle_id = "boot_health"
    oracle_name = "boot_health"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(self, *, timeout_ms: int = 1500) -> None:
        self._timeout_ms = int(timeout_ms)

    def pre_check(self, ctx: OracleContext) -> OracleEvidence:
        return self._check(ctx, phase="pre")

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        return self._check(ctx, phase="post")

    def _check(self, ctx: OracleContext, *, phase: str) -> OracleEvidence:
        boot_completed, meta = probe_boot_completed(ctx.controller, timeout_ms=self._timeout_ms)

        if boot_completed is None:
            conclusive = False
            success = False
            score = 0.0
            reason = "boot_completed probe unavailable"
        elif boot_completed:
            conclusive = True
            success = True
            score = 1.0
            reason = "boot completed"
        else:
            conclusive = True
            success = False
            score = 0.0
            reason = "boot not completed"

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase=phase,
                queries=[
                    make_query(
                        query_type="adb_cmd",
                        cmd="shell getprop sys.boot_completed",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={"boot_completed": boot_completed, "probe": meta},
                result_preview={"boot_completed": boot_completed},
                anti_gaming_notes=[
                    (
                        "Infrastructure probe: verifies the device has completed boot. "
                        "Runs that start before boot completion are attributed as infra_failed."
                    ),
                ],
                decision=make_decision(
                    success=success,
                    score=score,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]


@register_oracle(BootHealthOracle.oracle_id)
def _make_boot_health(cfg: Mapping[str, Any]) -> Oracle:
    timeout_ms = int(cfg.get("timeout_ms", 1500))
    return BootHealthOracle(timeout_ms=timeout_ms)


@register_oracle("BootHealthOracle")
def _make_boot_health_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_boot_health(cfg)
