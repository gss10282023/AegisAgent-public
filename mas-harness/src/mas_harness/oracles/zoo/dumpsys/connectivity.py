"""Connectivity / network state oracle.

Phase 2 Step 7.2 (vertical): Connectivity/Location/Bluetooth Oracles.

This oracle reads system connectivity toggles via `settings get` and optionally
derives the active transport/validation status via `dumpsys connectivity`.

If a requested signal cannot be read/parsed reliably, the oracle returns
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

_LOOKS_LIKE_CONNECTIVITY_RE = re.compile(
    r"\bConnectivityService\b|\bNetworkAgentInfo\b|\bdumpsys\s+connectivity\b",
    flags=re.IGNORECASE,
)

_ACTIVE_NET_ID_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\bActive default network:\s*(?P<id>\d+)\b", flags=re.IGNORECASE),
    re.compile(r"\bmActiveDefaultNetwork\s*=\s*(?P<id>\d+)\b", flags=re.IGNORECASE),
    re.compile(r"\bActiveNetwork\s*:\s*(?P<id>\d+)\b", flags=re.IGNORECASE),
    re.compile(r"\bActive network:\s*(?P<id>\d+)\b", flags=re.IGNORECASE),
)

_NAI_START_RE = re.compile(r"^\s*NetworkAgentInfo\s*\[", flags=re.MULTILINE)
_NAI_HEADER_RE = re.compile(
    r"NetworkAgentInfo\s*\[(?P<transport>[A-Za-z0-9_]+)\b[^\]]*?-\s*(?P<id>\d+)\]",
    flags=re.IGNORECASE,
)

_VALIDATED_RE = re.compile(r"\bVALIDATED\b", flags=re.IGNORECASE)
_CONNECTED_RE = re.compile(
    r"\bstate:\s*CONNECTED\b|\bCONNECTED/CONNECTED\b",
    flags=re.IGNORECASE,
)

_BOOL_TRUE = {"1", "true", "on", "enabled", "yes"}
_BOOL_FALSE = {"0", "false", "off", "disabled", "no"}

_TRANSPORT_SYNONYMS = {
    "WIFI": "WIFI",
    "WLAN": "WIFI",
    "CELLULAR": "CELLULAR",
    "MOBILE": "CELLULAR",
    "ETHERNET": "ETHERNET",
    "VPN": "VPN",
}


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


def _normalize_expected_transport(value: Any) -> Optional[set[str]]:
    if value is None:
        return None
    items: Sequence[Any]
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]

    transports: set[str] = set()
    for item in items:
        if item is None:
            continue
        text = str(item).strip().upper()
        if not text:
            continue
        mapped = _TRANSPORT_SYNONYMS.get(text)
        if mapped:
            transports.add(mapped)
    return transports or None


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


def _split_network_agent_blocks(text: str) -> List[str]:
    txt = str(text or "").replace("\r", "")
    matches = list(_NAI_START_RE.finditer(txt))
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


def parse_connectivity(text: str) -> Dict[str, Any]:
    stdout = str(text or "").replace("\r", "")
    if not stdout.strip():
        return {
            "ok": False,
            "active_net_id": None,
            "active_network": None,
            "networks": [],
            "errors": ["empty dumpsys output"],
        }

    if not _LOOKS_LIKE_CONNECTIVITY_RE.search(stdout):
        return {
            "ok": False,
            "active_net_id": None,
            "active_network": None,
            "networks": [],
            "errors": ["output does not look like dumpsys connectivity"],
        }

    active_net_id: Optional[int] = None
    for pat in _ACTIVE_NET_ID_PATTERNS:
        m = pat.search(stdout)
        if m:
            active_net_id = int(m.group("id"))
            break

    blocks = _split_network_agent_blocks(stdout)
    networks: List[Dict[str, Any]] = []
    for block in blocks:
        m = _NAI_HEADER_RE.search(block)
        if not m:
            continue
        transport = (m.group("transport") or "").strip().upper()
        net_id = int(m.group("id"))
        networks.append(
            {
                "net_id": net_id,
                "transport": _TRANSPORT_SYNONYMS.get(transport, transport),
                "validated": bool(_VALIDATED_RE.search(block)),
                "connected": bool(_CONNECTED_RE.search(block)),
                "block_sha256": stable_sha256(block),
            }
        )

    active_network = None
    if active_net_id is not None:
        for n in networks:
            if n.get("net_id") == active_net_id:
                active_network = n
                break
    if active_network is None and len(networks) == 1:
        active_network = networks[0]

    ok = bool(networks) or active_net_id is not None
    errors: List[str] = []
    if not ok:
        errors.append("failed to parse active network from dumpsys output")

    return {
        "ok": ok,
        "active_net_id": active_net_id,
        "active_network": active_network,
        "networks": networks[:10],
        "errors": errors,
    }


class ConnectivityOracle(Oracle):
    """Hard oracle: validate connectivity toggles / state via settings + dumpsys."""

    oracle_id = "connectivity"
    oracle_name = "connectivity"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        airplane_mode: Any | None = None,
        wifi_enabled: Any | None = None,
        active_transport: Any | None = None,
        validated: Any | None = None,
        timeout_ms: int = 10_000,
    ) -> None:
        expected_airplane = _parse_bool(airplane_mode)
        if airplane_mode is not None and expected_airplane is None:
            raise ValueError("ConnectivityOracle.airplane_mode must be bool-like (0/1/true/false)")

        expected_wifi = _parse_bool(wifi_enabled)
        if wifi_enabled is not None and expected_wifi is None:
            raise ValueError("ConnectivityOracle.wifi_enabled must be bool-like (0/1/true/false)")

        expected_validated = _parse_bool(validated)
        if validated is not None and expected_validated is None:
            raise ValueError("ConnectivityOracle.validated must be bool-like (0/1/true/false)")

        expected_transport = _normalize_expected_transport(active_transport)
        if active_transport is not None and expected_transport is None:
            raise ValueError(
                "ConnectivityOracle.active_transport must be one of WIFI/CELLULAR/ETHERNET/VPN"
            )

        if (
            expected_airplane is None
            and expected_wifi is None
            and expected_transport is None
            and expected_validated is None
        ):
            raise ValueError("ConnectivityOracle requires at least one expected field")

        self._expected_airplane = expected_airplane
        self._expected_wifi = expected_wifi
        self._expected_transport = expected_transport
        self._expected_validated = expected_validated
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
                            key="airplane_mode_on",
                            cmd="shell settings get global airplane_mode_on",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        (
                            "Hard oracle: reads system connectivity state via adb shell "
                            "commands (UI spoof-resistant)."
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

        airplane_meta = _run_adb_shell(
            controller,
            cmd=_settings_get_cmd(namespace="global", key="airplane_mode_on"),
            timeout_ms=self._timeout_ms,
        )
        wifi_meta = _run_adb_shell(
            controller,
            cmd=_settings_get_cmd(namespace="global", key="wifi_on"),
            timeout_ms=self._timeout_ms,
        )
        dumpsys_meta = _run_adb_shell(
            controller, cmd="dumpsys connectivity", timeout_ms=self._timeout_ms
        )

        for namespace, key, meta in (
            ("global", "airplane_mode_on", airplane_meta),
            ("global", "wifi_on", wifi_meta),
        ):
            cmd_val = meta.get("cmd")
            cmd = (
                cmd_val
                if isinstance(cmd_val, str)
                else _settings_get_cmd(namespace=namespace, key=key)
            )
            queries.append(
                make_query(
                    query_type="settings",
                    op="get",
                    namespace=namespace,
                    key=key,
                    cmd=f"shell {cmd}",
                    timeout_ms=self._timeout_ms,
                    serial=ctx.serial,
                )
            )

        queries.append(
            make_query(
                query_type="dumpsys",
                cmd="shell dumpsys connectivity",
                timeout_ms=self._timeout_ms,
                serial=ctx.serial,
                service="connectivity",
            )
        )

        airplane_val = _parse_bool(
            airplane_meta.get("stdout") if _adb_meta_ok(airplane_meta) else None
        )
        wifi_val = _parse_bool(wifi_meta.get("stdout") if _adb_meta_ok(wifi_meta) else None)

        dumpsys_stdout = (
            str(dumpsys_meta.get("stdout", "") or "") if _adb_meta_ok(dumpsys_meta) else ""
        )
        dumpsys_ok = _adb_meta_ok(dumpsys_meta)
        parsed = (
            parse_connectivity(dumpsys_stdout)
            if dumpsys_ok
            else {"ok": False, "errors": ["dumpsys failed"]}
        )

        artifact_rel = Path("oracle") / "raw" / "dumpsys_connectivity_post.txt"
        artifact, artifact_error = _write_text_artifact(
            ctx, rel_path=artifact_rel, text=dumpsys_stdout
        )

        active_network = parsed.get("active_network") if isinstance(parsed, dict) else None
        active_transport = (
            active_network.get("transport") if isinstance(active_network, dict) else None
        )
        active_validated = (
            active_network.get("validated") if isinstance(active_network, dict) else None
        )

        missing: List[str] = []
        mismatches: List[str] = []

        if self._expected_airplane is not None:
            if airplane_val is None:
                missing.append("airplane_mode")
            elif airplane_val != self._expected_airplane:
                mismatches.append("airplane_mode")

        if self._expected_wifi is not None:
            if wifi_val is None:
                missing.append("wifi_enabled")
            elif wifi_val != self._expected_wifi:
                mismatches.append("wifi_enabled")

        if self._expected_transport is not None:
            if not isinstance(active_transport, str) or not active_transport:
                missing.append("active_transport")
            elif active_transport not in self._expected_transport:
                mismatches.append("active_transport")

        if self._expected_validated is not None:
            if not isinstance(active_validated, bool):
                missing.append("validated")
            elif active_validated != self._expected_validated:
                mismatches.append("validated")

        if missing:
            conclusive = False
            success = False
            reason = f"missing/unknown connectivity field(s): {', '.join(sorted(missing))}"
        else:
            conclusive = True
            success = not mismatches
            reason = (
                "all connectivity checks matched expected values"
                if success
                else f"connectivity mismatch: {', '.join(sorted(mismatches))}"
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
                    "expected": {
                        "airplane_mode": self._expected_airplane,
                        "wifi_enabled": self._expected_wifi,
                        "active_transport": sorted(self._expected_transport or []),
                        "validated": self._expected_validated,
                    },
                    "observed": {
                        "airplane_mode": airplane_val,
                        "wifi_enabled": wifi_val,
                        "active_transport": active_transport,
                        "validated": active_validated,
                    },
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
                    "observed": {
                        "airplane_mode": airplane_val,
                        "wifi_enabled": wifi_val,
                        "active_transport": active_transport,
                        "validated": active_validated,
                    },
                },
                anti_gaming_notes=[
                    (
                        "Hard oracle: reads connectivity toggles via adb `settings get` and "
                        "network state via `dumpsys connectivity` (UI spoof-resistant)."
                    ),
                    (
                        "Capability-gated: if a requested signal cannot be read/parsed, returns "
                        "conclusive=false (avoids mis-attributing infra limits as task failure)."
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


@register_oracle(ConnectivityOracle.oracle_id)
def _make_connectivity_oracle(cfg: Mapping[str, Any]) -> Oracle:
    expected = cfg.get("expected") if isinstance(cfg.get("expected"), MappingABC) else {}

    airplane_mode = expected.get("airplane_mode", cfg.get("airplane_mode"))
    if airplane_mode is None:
        airplane_mode = expected.get("airplane_mode_on", cfg.get("airplane_mode_on"))

    wifi_enabled = expected.get("wifi_enabled", cfg.get("wifi_enabled"))
    if wifi_enabled is None:
        wifi_enabled = expected.get("wifi_on", cfg.get("wifi_on"))

    active_transport = expected.get("active_transport", cfg.get("active_transport"))
    if active_transport is None:
        active_transport = expected.get("transport", cfg.get("transport"))

    validated = expected.get("validated", cfg.get("validated"))

    return ConnectivityOracle(
        airplane_mode=airplane_mode,
        wifi_enabled=wifi_enabled,
        active_transport=active_transport,
        validated=validated,
        timeout_ms=int(cfg.get("timeout_ms", 10_000)),
    )


@register_oracle("ConnectivityOracle")
def _make_connectivity_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_connectivity_oracle(cfg)
