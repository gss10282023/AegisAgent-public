"""Environment capabilities helpers for Oracle Zoo.

Step 5.2: standardize `env_capabilities.json` fields so oracle capability gating
is explainable and consistent across environments.

The run-level `env_capabilities.json` is a Phase-0 governance artifact (written
once per run). This module provides lightweight helpers to:
  - locate/load the file from an episode dir
  - read the standardized capability keys
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

STANDARD_CAPABILITY_KEYS: tuple[str, ...] = (
    "root_available",
    "run_as_available",
    "can_pull_data",
    "sdcard_writable",
    "host_artifacts_available",
    "android_api_level",
)


def find_run_root(episode_dir: Path) -> Optional[Path]:
    """Find the run root containing run-level artifacts (best-effort)."""

    p = Path(episode_dir).resolve()
    for candidate in (p, *p.parents):
        if (candidate / "env_capabilities.json").exists() and (
            candidate / "run_manifest.json"
        ).exists():
            return candidate
    return None


def load_env_capabilities(run_root: Path) -> Optional[Dict[str, Any]]:
    """Load run-level env_capabilities.json (best-effort)."""

    try:
        raw = (Path(run_root) / "env_capabilities.json").read_text(encoding="utf-8")
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def standard_capabilities(env_caps: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Extract standardized capability flags from an env_capabilities object.

    Always returns a dict containing all STANDARD_CAPABILITY_KEYS.
    """

    out: Dict[str, Any] = {k: None for k in STANDARD_CAPABILITY_KEYS}
    if env_caps is None:
        return out

    caps_obj = env_caps.get("capabilities")
    if isinstance(caps_obj, Mapping):
        for k in STANDARD_CAPABILITY_KEYS:
            if k in caps_obj:
                out[k] = caps_obj.get(k)

    # Backward compatibility: infer from legacy structure if needed.
    android = env_caps.get("android") if isinstance(env_caps.get("android"), Mapping) else {}
    host = (
        env_caps.get("host_artifacts")
        if isinstance(env_caps.get("host_artifacts"), Mapping)
        else {}
    )

    if out.get("root_available") is None:
        out["root_available"] = android.get("root_available")
    if out.get("run_as_available") is None:
        out["run_as_available"] = android.get("run_as_available")
    if out.get("can_pull_data") is None:
        out["can_pull_data"] = android.get("can_pull_data")
    if out.get("sdcard_writable") is None:
        out["sdcard_writable"] = android.get("sdcard_writable")
    if out.get("android_api_level") is None:
        out["android_api_level"] = android.get("android_api_level")
    if out.get("host_artifacts_available") is None:
        out["host_artifacts_available"] = host.get("host_artifacts_available")

    return out


def format_missing_capabilities_reason(missing: Sequence[str], *, detail: str) -> str:
    """Format a deterministic, readable reason string for missing capabilities."""

    missing_list = [str(x) for x in missing if str(x).strip()]
    prefix = ", ".join(missing_list) if missing_list else "capability"
    suffix = str(detail).strip()
    if not suffix:
        return f"missing {prefix}"
    return f"missing {prefix}: {suffix}"
