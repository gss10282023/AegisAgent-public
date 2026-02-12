"""Reset/Snapshot grounding helpers (Phase 2 Step 3).

This module centralizes reset behavior so runners can enforce a stable lifecycle:

  reset -> pre_oracles -> run -> post_oracles -> write summary

For Android, the preferred reset strategy is loading an AVD snapshot. For toy
Phase-0 runs, this becomes a no-op (but the reset event is still recorded).
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

ResetStrategy = Literal["snapshot", "reinstall", "none"]


def resolve_reset_strategy(
    *, reset_strategy: Optional[str], snapshot_tag: Optional[str]
) -> ResetStrategy:
    if reset_strategy is not None:
        normalized = str(reset_strategy).strip().lower()
        if normalized in {"snapshot", "reinstall", "none"}:
            return normalized  # type: ignore[return-value]
    if snapshot_tag:
        return "snapshot"
    return "none"


def _maybe_call_reset(controller: Any, initial_state: Any | None) -> bool:
    if not hasattr(controller, "reset"):
        return False
    reset_fn = getattr(controller, "reset")
    try:
        reset_fn(initial_state=initial_state)
    except TypeError:
        reset_fn()
    return True


def _maybe_get_fingerprint(controller: Any) -> Optional[str]:
    if not hasattr(controller, "get_build_fingerprint"):
        return None
    try:
        fp = getattr(controller, "get_build_fingerprint")()
    except Exception:
        return None
    fp = str(fp).strip()
    return fp or None


def reset_for_episode(
    *,
    controller: Any,
    initial_state: Any | None,
    reset_strategy: Optional[str],
    snapshot_tag: Optional[str],
) -> Dict[str, Any]:
    """Execute reset/snapshot (best-effort) and return an evidence event payload."""

    strategy = resolve_reset_strategy(reset_strategy=reset_strategy, snapshot_tag=snapshot_tag)
    event: Dict[str, Any] = {
        "reset_strategy": strategy,
        "snapshot_tag": snapshot_tag,
    }

    if strategy == "snapshot":
        if snapshot_tag and hasattr(controller, "load_snapshot"):
            try:
                res = getattr(controller, "load_snapshot")(snapshot_tag)
                if hasattr(res, "stdout") or hasattr(res, "returncode") or hasattr(res, "args"):
                    event["snapshot_load"] = {
                        "args": getattr(res, "args", None),
                        "returncode": getattr(res, "returncode", None),
                        "stdout": getattr(res, "stdout", None),
                        "stderr": getattr(res, "stderr", None),
                    }
                else:
                    event["snapshot_load"] = {"result": str(res)}
            except Exception as e:
                event["snapshot_load_error"] = repr(e)
        else:
            event["snapshot_load_skipped"] = True
            if not snapshot_tag:
                event["snapshot_load_skip_reason"] = "missing_snapshot_tag"
            else:
                event["snapshot_load_skip_reason"] = "missing_controller_capability: load_snapshot"

    event["controller_reset_called"] = _maybe_call_reset(controller, initial_state)
    event["emulator_fingerprint"] = _maybe_get_fingerprint(controller)
    return event
