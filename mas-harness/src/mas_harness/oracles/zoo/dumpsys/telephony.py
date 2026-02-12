"""Telephony dumpsys oracles.

Phase 2 Step 10 (Oracle Zoo v1, C 类): Dumpsys Oracles.

Implements TelephonyCallStateOracle:
  - `adb shell dumpsys telephony.registry`
  - Parse call state (IDLE/OFFHOOK/RINGING)
  - Store raw dumpsys output as an artifact; write structured fields into evidence.
"""

from __future__ import annotations

import re
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

_CALL_STATE_BY_CODE = {
    0: "IDLE",
    1: "RINGING",
    2: "OFFHOOK",
}

_CALL_STATE_CODE_BY_NAME = {v: k for k, v in _CALL_STATE_BY_CODE.items()}

_CALL_STATE_RE = re.compile(
    r"\bmCallState\s*(?:=|:)\s*(?P<state>\d+|IDLE|OFFHOOK|RINGING)\b",
    flags=re.IGNORECASE,
)


def _normalize_expected_call_states(value: Any) -> Tuple[set[int], set[str]]:
    expected_codes: set[int] = set()
    expected_labels: set[str] = set()

    if value is None:
        return expected_codes, expected_labels

    if isinstance(value, (list, tuple, set)):
        items: Sequence[Any] = list(value)
    else:
        items = [value]

    for item in items:
        if item is None:
            continue
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            expected_codes.add(int(item))
            label = _CALL_STATE_BY_CODE.get(int(item))
            if label:
                expected_labels.add(label)
            continue

        text = str(item).strip()
        if not text:
            continue
        if text.isdigit():
            code = int(text)
            expected_codes.add(code)
            label = _CALL_STATE_BY_CODE.get(code)
            if label:
                expected_labels.add(label)
            continue

        label = text.upper()
        if label in _CALL_STATE_CODE_BY_NAME:
            expected_labels.add(label)
            expected_codes.add(_CALL_STATE_CODE_BY_NAME[label])

    return expected_codes, expected_labels


def _parse_call_state(stdout: str) -> Dict[str, Any]:
    txt = (stdout or "").replace("\r", "")
    matches: list[dict[str, Any]] = []
    for m in _CALL_STATE_RE.finditer(txt):
        raw = (m.group("state") or "").strip()
        code: Optional[int] = None
        label: Optional[str] = None
        if raw.isdigit():
            code = int(raw)
            label = _CALL_STATE_BY_CODE.get(code)
        else:
            label = raw.upper()
            code = _CALL_STATE_CODE_BY_NAME.get(label)
        matches.append(
            {
                "raw": raw,
                "code": code,
                "label": label,
            }
        )

    primary = matches[0] if matches else None
    return {
        "call_state_code": (primary.get("code") if isinstance(primary, dict) else None),
        "call_state": (primary.get("label") if isinstance(primary, dict) else None),
        "match_count": len(matches),
        "matches": matches[:10],
    }


def _run_dumpsys(controller: Any, *, service: str, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "service": str(service),
        "cmd": f"dumpsys {service}",
        "timeout_ms": int(timeout_ms),
    }

    result: Any
    try:
        if hasattr(controller, "dumpsys"):
            meta["api"] = "dumpsys"
            try:
                result = controller.dumpsys(
                    str(service), timeout_s=float(timeout_ms) / 1000.0, check=False
                )
            except TypeError:
                result = controller.dumpsys(str(service))
        elif hasattr(controller, "adb_shell"):
            meta["api"] = "adb_shell"
            try:
                result = controller.adb_shell(
                    f"dumpsys {service}",
                    timeout_ms=int(timeout_ms),
                    check=False,
                )
            except TypeError:
                result = controller.adb_shell(
                    f"dumpsys {service}",
                    timeout_s=float(timeout_ms) / 1000.0,
                    check=False,
                )
        else:
            meta["api"] = None
            meta["missing"] = ["dumpsys", "adb_shell"]
            return meta
    except Exception as e:  # pragma: no cover
        meta["exception"] = repr(e)
        meta["returncode"] = None
        meta["stdout"] = ""
        meta["stderr"] = None
        meta["args"] = None
        return meta

    if hasattr(result, "stdout") or hasattr(result, "returncode"):
        meta.update(
            {
                "args": getattr(result, "args", None),
                "returncode": getattr(result, "returncode", None),
                "stderr": getattr(result, "stderr", None),
                "stdout": str(getattr(result, "stdout", "") or ""),
            }
        )
        return meta

    meta.update({"returncode": 0, "stdout": str(result), "stderr": None, "args": None})
    return meta


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


class TelephonyCallStateOracle(Oracle):
    """Read telephony call state from `dumpsys telephony.registry`."""

    oracle_id = "telephony_call_state"
    oracle_name = "telephony_call_state"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        expected: Any = "IDLE",
        timeout_ms: int = 5000,
    ) -> None:
        expected_codes, expected_labels = _normalize_expected_call_states(expected)
        if not expected_codes and not expected_labels:
            raise ValueError("TelephonyCallStateOracle requires non-empty expected call state(s)")

        self._expected_codes = expected_codes
        self._expected_labels = expected_labels
        self._timeout_ms = int(timeout_ms)

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        controller = ctx.controller

        if not (hasattr(controller, "dumpsys") or hasattr(controller, "adb_shell")):
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
                            cmd="shell dumpsys telephony.registry",
                            timeout_ms=self._timeout_ms,
                            serial=ctx.serial,
                            service="telephony.registry",
                        )
                    ],
                    result_for_digest={
                        "missing": ["adb_shell"],
                        "reason": "controller lacks dumpsys/adb_shell",
                    },
                    anti_gaming_notes=[
                        (
                            "Hard oracle: reads telephony call state via adb dumpsys "
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

        meta = _run_dumpsys(controller, service="telephony.registry", timeout_ms=self._timeout_ms)
        stdout = str(meta.get("stdout", "") or "")
        dumpsys_ok = _dumpsys_meta_ok(meta)

        artifact_rel = Path("oracle_artifacts") / "dumpsys_telephony.registry_post.txt"
        artifact, artifact_error = _write_text_artifact(ctx, rel_path=artifact_rel, text=stdout)

        parsed = _parse_call_state(stdout)
        call_state = parsed.get("call_state")
        call_state_code = parsed.get("call_state_code")
        parsed_ok = bool(
            call_state in _CALL_STATE_CODE_BY_NAME and call_state_code in _CALL_STATE_BY_CODE
        )

        if not dumpsys_ok:
            conclusive = False
            success = False
            reason = "dumpsys telephony.registry failed"
        elif not parsed_ok:
            conclusive = False
            success = False
            reason = "failed to parse call state from dumpsys output"
        else:
            matched = (call_state in self._expected_labels) or (
                isinstance(call_state_code, int) and call_state_code in self._expected_codes
            )
            conclusive = True
            success = bool(matched)
            reason = (
                f"call_state matched expected: {call_state}"
                if matched
                else f"call_state mismatch: {call_state} (expected {sorted(self._expected_labels)})"
            )

        result_preview: Dict[str, Any] = {
            "service": "telephony.registry",
            "expected": sorted(self._expected_labels),
            "dumpsys_ok": dumpsys_ok,
            "call_state": call_state,
            "call_state_code": call_state_code,
            "match_count": parsed.get("match_count"),
            "artifact": artifact,
            "artifact_error": artifact_error,
        }

        artifacts = [artifact] if artifact is not None else None

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
                        cmd="shell dumpsys telephony.registry",
                        timeout_ms=self._timeout_ms,
                        serial=ctx.serial,
                        service="telephony.registry",
                    )
                ],
                result_for_digest={
                    "service": "telephony.registry",
                    "expected_codes": sorted(self._expected_codes),
                    "expected_labels": sorted(self._expected_labels),
                    "dumpsys_ok": dumpsys_ok,
                    "meta": {k: v for k, v in meta.items() if k != "stdout"},
                    "stdout_sha256": stable_sha256(stdout),
                    "stdout_len": len(stdout),
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                    "parsed": parsed,
                },
                result_preview=result_preview,
                anti_gaming_notes=[
                    "Hard oracle: reads telephony call state via adb dumpsys (UI spoof-resistant).",
                    (
                        "Evidence hygiene: stores raw dumpsys output as an artifact and "
                        "records only structured fields + digests in evidence."
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


@register_oracle(TelephonyCallStateOracle.oracle_id)
def _make_telephony_call_state(cfg: Mapping[str, Any]) -> Oracle:
    expected = cfg.get("expected") or cfg.get("expect") or cfg.get("call_state") or "IDLE"
    timeout_ms = int(cfg.get("timeout_ms", 5000))
    return TelephonyCallStateOracle(expected=expected, timeout_ms=timeout_ms)


@register_oracle("TelephonyCallStateOracle")
def _make_telephony_call_state_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_telephony_call_state(cfg)


@register_oracle("TelephonyDumpsysOracle")
def _make_telephony_dumpsys_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_telephony_call_state(cfg)
