from __future__ import annotations

from typing import Any, Literal, Mapping

RefObsDigestDecision = Literal["pass", "fail", "not_applicable"]


def is_ref_check_applicable(action: Mapping[str, Any]) -> bool:
    """Return whether ref_obs_digest-based checks should be applied to this action.

    Phase3 audit_only ingestion may intentionally downgrade ref checks when
    observation evidence is missing (ref_check_applicable=false).
    """

    raw = action.get("ref_check_applicable")
    if isinstance(raw, bool):
        return raw

    if action.get("auditability_limited") is True:
        return False

    ref_obs_digest = action.get("ref_obs_digest")
    return isinstance(ref_obs_digest, str) and bool(ref_obs_digest.strip())


def ref_obs_digest_consistency_decision(
    action: Mapping[str, Any],
    *,
    current_obs_digest: str | None,
) -> RefObsDigestDecision:
    """Check whether action.ref_obs_digest matches `current_obs_digest`.

    Returns:
      - "not_applicable" when the action indicates ref checks are not applicable
      - "pass" when applicable and digests match
      - "fail" when applicable and digests are missing/mismatched
    """

    if not is_ref_check_applicable(action):
        return "not_applicable"

    ref_obs_digest = action.get("ref_obs_digest")
    if not isinstance(ref_obs_digest, str) or not ref_obs_digest.strip():
        return "fail"

    if not isinstance(current_obs_digest, str) or not current_obs_digest.strip():
        return "fail"

    return "pass" if ref_obs_digest == current_obs_digest else "fail"
