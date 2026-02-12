"""Generic adb-shell based hard oracles."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping

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


class AdbShellExpectRegexOracle(Oracle):
    """Hard oracle that runs an adb shell command and matches a regex.

    This is a generic building block (borrowed from AndroidWorld/MobileWorld style
    validators). It is intentionally conservative: it only exposes a redacted
    preview in evidence; full output can be stored by setting store_full_output.
    """

    oracle_id = "adb_shell_expect_regex"
    oracle_name = "adb_shell_expect_regex"
    oracle_type = "hard"
    capabilities_required = ("adb_shell",)

    def __init__(
        self,
        *,
        shell_cmd: str,
        expect_regex: str,
        flags: int = 0,
        store_full_output: bool = False,
        timeout_s: int = 30,
    ):
        self._shell_cmd = shell_cmd
        self._expect = re.compile(expect_regex, flags)
        self._store_full = store_full_output
        self._timeout_s = timeout_s

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        # Expect controller to provide adb_shell(cmd, ...).
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
                            query_type="adb_cmd",
                            cmd=f"shell {self._shell_cmd}",
                            timeout_ms=self._timeout_s * 1000,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["adb_shell"]},
                    anti_gaming_notes=[
                        "Hard oracle: checks device state via adb; robust to UI spoofing.",
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

        try:
            adb_result = controller.adb_shell(self._shell_cmd, timeout_s=self._timeout_s)
        except TypeError:
            adb_result = controller.adb_shell(self._shell_cmd)

        stdout: str
        stderr: str | None = None
        returncode: int | None = None
        args: Any = None
        if hasattr(adb_result, "stdout"):
            stdout = str(getattr(adb_result, "stdout", ""))
            stderr = getattr(adb_result, "stderr", None)
            returncode = getattr(adb_result, "returncode", None)
            args = getattr(adb_result, "args", None)
        else:
            stdout = str(adb_result)

        m = self._expect.search(stdout)
        matched = bool(m)

        preview: Dict[str, Any] = {
            "matched": matched,
            "match_groups": m.groupdict() if m else {},
        }
        if self._store_full:
            stdout_preview = stdout if len(stdout) <= 10_000 else stdout[:10_000] + "...(truncated)"
            preview["stdout"] = stdout_preview
            preview["stdout_truncated"] = len(stdout) > 10_000

        result_for_digest = {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
            "args": args,
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
                        cmd=f"shell {self._shell_cmd}",
                        timeout_ms=self._timeout_s * 1000,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest=result_for_digest,
                result_preview=preview,
                anti_gaming_notes=[
                    "Hard oracle: checks device state via adb; robust to UI spoofing.",
                ],
                decision=make_decision(
                    success=matched,
                    score=1.0 if matched else 0.0,
                    reason="regex matched" if matched else "regex did not match",
                    conclusive=True,
                ),
                capabilities_required=list(self.capabilities_required),
            )
        ]


@register_oracle(AdbShellExpectRegexOracle.oracle_id)
def _make_adb_shell_expect_regex(cfg: Mapping[str, Any]) -> Oracle:
    shell_cmd = str(cfg["shell_cmd"])
    expect_regex = str(cfg["expect_regex"])
    store_full = bool(cfg.get("store_full_output", False))
    timeout_s = int(cfg.get("timeout_s", 30))
    return AdbShellExpectRegexOracle(
        shell_cmd=shell_cmd,
        expect_regex=expect_regex,
        store_full_output=store_full,
        timeout_s=timeout_s,
    )
