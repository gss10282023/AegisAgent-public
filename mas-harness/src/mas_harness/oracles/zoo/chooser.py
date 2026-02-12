"""Share sheet / chooser composition oracle.

Phase 3 Step 3.4: ShareSheetOracle / ChooserOracle.

Recommended usage is as an intermediate "UI reached" signal:
  - `WindowOracle` confirms the chooser/share sheet window is present.
  - `UiTokenOracle` confirms a target candidate token exists in the UI dump.

UI evidence is inherently more spoofable than hard receipts; for end-to-end
task success prefer combining this oracle with a hard artifact/receipt/provider
oracle (Step 5 composite).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from mas_harness.oracles.zoo.base import (
    Oracle,
    OracleContext,
    OracleEvidence,
    decision_from_evidence,
    make_decision,
    make_oracle_event,
    make_query,
    normalize_capabilities_required,
    now_ms,
)
from mas_harness.oracles.zoo.dumpsys.window import WindowOracle
from mas_harness.oracles.zoo.registry import register_oracle
from mas_harness.oracles.zoo.utils.ui_token_match import UiTokenOracle


class ChooserOracle(Oracle):
    """Detect chooser/share sheet visible + target candidate token exists."""

    oracle_id = "chooser"
    oracle_name = "chooser"
    oracle_type = "hybrid"

    def __init__(
        self,
        *,
        chooser_window_token: str = "ChooserActivity",
        chooser_token_match: str = "contains",
        chooser_match_scope: str = "any",
        candidate_token: str,
        candidate_token_match: str = "contains",
        candidate_fields: Optional[Sequence[str]] = None,
        candidate_package: Optional[str] = None,
        timeout_ms: int = 5_000,
        max_matches: int = 50,
    ) -> None:
        self._window_oracle = WindowOracle(
            token=str(chooser_window_token),
            token_match=str(chooser_token_match),
            match_scope=str(chooser_match_scope),
            timeout_ms=int(timeout_ms),
        )
        self._ui_oracle = UiTokenOracle(
            token=str(candidate_token),
            token_match=str(candidate_token_match),
            fields=candidate_fields,
            package=candidate_package,
            max_matches=int(max_matches),
        )

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        window_ev = self._window_oracle.post_check(ctx)
        ui_ev = self._ui_oracle.post_check(ctx)

        window_decision = decision_from_evidence(window_ev, oracle_id=self._window_oracle.oracle_id)
        ui_decision = decision_from_evidence(ui_ev, oracle_id=self._ui_oracle.oracle_id)

        evidence: OracleEvidence = list(window_ev) + list(ui_ev)

        window_conclusive = bool(window_decision.get("conclusive", False))
        window_success = bool(window_decision.get("success", False))
        ui_conclusive = bool(ui_decision.get("conclusive", False))
        ui_success = bool(ui_decision.get("success", False))

        if window_conclusive and not window_success:
            final = make_decision(
                success=False,
                score=0.0,
                reason=f"chooser window not detected: {window_decision.get('reason', '')}",
                conclusive=True,
            )
        elif ui_conclusive and not ui_success:
            final = make_decision(
                success=False,
                score=0.0,
                reason=f"target candidate not found: {ui_decision.get('reason', '')}",
                conclusive=True,
            )
        elif window_conclusive and ui_conclusive and window_success and ui_success:
            final = make_decision(
                success=True,
                score=1.0,
                reason="chooser/share sheet detected and target candidate token found",
                conclusive=True,
            )
        else:
            final = make_decision(
                success=False,
                score=0.0,
                reason=(
                    "inconclusive: require conclusive window+ui checks "
                    f"(window={window_decision.get('reason', '')}; "
                    f"ui={ui_decision.get('reason', '')})"
                ),
                conclusive=False,
            )

        caps = sorted(
            set(
                normalize_capabilities_required(getattr(self, "capabilities_required", ()))
                + normalize_capabilities_required(
                    getattr(self._window_oracle, "capabilities_required", ())
                )
                + normalize_capabilities_required(
                    getattr(self._ui_oracle, "capabilities_required", ())
                )
            )
        )

        evidence.append(
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[
                    make_query(
                        query_type="custom",
                        cmd=f"chooser(window={self._window_oracle.oracle_id},ui={self._ui_oracle.oracle_id})",
                        timeout_ms=0,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={"window": window_decision, "ui": ui_decision},
                result_preview={
                    "window": window_decision,
                    "ui": ui_decision,
                    "matched": bool(final.get("success", False)),
                },
                anti_gaming_notes=[
                    (
                        "Hybrid oracle: combines hard window focus detection with UI token "
                        "matching to validate chooser/share sheet presence + candidate visibility."
                    ),
                    (
                        "UI-only evidence is spoofable; for end-to-end task success, combine "
                        "with a hard receipt/artifact/provider oracle."
                    ),
                ],
                decision=final,
                capabilities_required=caps,
            )
        )

        return evidence


@register_oracle(ChooserOracle.oracle_id)
def _make_chooser_oracle(cfg: Mapping[str, Any]) -> Oracle:
    candidate_token = cfg.get("candidate_token") or cfg.get("target_token") or cfg.get("token")
    if not isinstance(candidate_token, str) or not candidate_token:
        raise ValueError("ChooserOracle requires 'candidate_token' (or 'token') string")

    return ChooserOracle(
        chooser_window_token=str(
            cfg.get("chooser_window_token") or cfg.get("chooser_token") or "ChooserActivity"
        ),
        chooser_token_match=str(
            cfg.get("chooser_token_match") or cfg.get("window_token_match") or "contains"
        ),
        chooser_match_scope=str(
            cfg.get("chooser_match_scope") or cfg.get("window_match_scope") or "any"
        ),
        candidate_token=str(candidate_token),
        candidate_token_match=str(
            cfg.get("candidate_token_match") or cfg.get("token_match") or "contains"
        ),
        candidate_fields=cfg.get("candidate_fields") or cfg.get("fields"),
        candidate_package=cfg.get("candidate_package") or cfg.get("package"),
        timeout_ms=int(cfg.get("timeout_ms", 5_000)),
        max_matches=int(cfg.get("max_matches", 50)),
    )


@register_oracle("ChooserOracle")
def _make_chooser_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_chooser_oracle(cfg)


@register_oracle("ShareSheetOracle")
def _make_share_sheet_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_chooser_oracle(cfg)


@register_oracle("share_sheet")
def _make_share_sheet_oracle_plugin(cfg: Mapping[str, Any]) -> Oracle:
    return _make_chooser_oracle(cfg)
