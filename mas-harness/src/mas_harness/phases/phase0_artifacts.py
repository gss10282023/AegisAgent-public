"""Phase 0 governance artifacts.

Phase 0 (v3.1) requires every run (even the toy smoke run) to write:

* run_manifest.json
* env_capabilities.json

These files make runs auditable and reproducible. They are intentionally
best-effort: when Android is not available (e.g., in CI), the files are still
generated with `android.available=false`.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from mas_harness.evidence import stable_file_sha256, stable_sha256


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(_json_dumps_canonical(obj) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _write_json(path: Path, obj: Any) -> None:
    _write_json_atomic(path, obj)


def _try_read_json_object(path: Path) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _iter_episode_dirs(run_dir: Path) -> list[Path]:
    if not run_dir.exists():
        return []
    out: list[Path] = []
    for p in run_dir.iterdir():
        if p.is_dir() and p.name.startswith("episode_"):
            out.append(p)
    return sorted(out)


def _collect_device_input_trace_source_levels(path: Path) -> set[str]:
    levels: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        lvl = obj.get("source_level")
        if isinstance(lvl, str) and lvl in {"L0", "L1", "L2"}:
            levels.add(lvl)
    return levels


def _action_trace_source_for_level(level: str) -> str:
    if level == "L0":
        return "mas_executor"
    if level == "L1":
        return "agent_events"
    if level == "L2":
        return "comm_proxy"
    return "none"


def _quarantine_invalid_device_input_trace(path: Path) -> None:
    if not path.exists():
        return
    invalid_path = path.with_name(f"{path.stem}.invalid{path.suffix}")
    if invalid_path.exists():
        i = 1
        while True:
            cand = path.with_name(f"{path.stem}.invalid{i}{path.suffix}")
            if not cand.exists():
                invalid_path = cand
                break
            i += 1
    path.replace(invalid_path)
    path.write_text("", encoding="utf-8")


def _try_run(cmd: list[str], *, timeout_s: float = 10.0) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "timeout"}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "returncode": None, "stdout": "", "stderr": repr(e)}


def _read_git_commit(repo_root: Path) -> Optional[str]:
    """Best-effort git commit hash.

    Works both with `git` and without it (by reading .git/HEAD).
    """

    # 1) Prefer `git` if available.
    out = _try_run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], timeout_s=3.0)
    if out.get("ok") and out.get("stdout"):
        return str(out["stdout"]).strip()

    # 2) Fallback: parse .git/HEAD.
    head = repo_root / ".git" / "HEAD"
    if not head.exists():
        return None
    ref = head.read_text(encoding="utf-8").strip()
    if ref.startswith("ref:"):
        ref_path = ref.split(" ", 1)[1].strip()
        ref_file = repo_root / ".git" / ref_path
        if ref_file.exists():
            return ref_file.read_text(encoding="utf-8").strip()
        packed = repo_root / ".git" / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or " " not in line:
                    continue
                sha, name = line.split(" ", 1)
                if name.strip() == ref_path:
                    return sha.strip()
        return None

    # Detached head
    if len(ref) >= 7:
        return ref
    return None


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


_AVAILABILITY_VALUES = {"runnable", "audit_only", "unavailable"}
_ACTION_TRACE_LEVEL_VALUES = {"L0", "L1", "L2", "L3", "none"}
_ACTION_TRACE_SOURCE_VALUES = {
    "mas_executor",
    "agent_events",
    "comm_proxy",
    "system_capture",
    "none",
}
_EVAL_MODE_VALUES = {"vanilla", "guarded"}
_GUARD_ENFORCEMENT_VALUES = {"enforced", "unenforced"}
_GUARD_UNENFORCED_REASON_VALUES = {
    "guard_disabled",
    "not_planner_only",
    "not_L0",
    "unknown",
}
_EVIDENCE_TRUST_LEVEL_VALUES = {"tcb_captured", "agent_reported", "unknown"}
_ORACLE_SOURCE_VALUES = {"device_query", "trajectory_declared", "none"}
_RUN_PURPOSE_VALUES = {"benchmark", "conformance", "agentctl_fixed", "agentctl_nl", "ingest_only"}


def _normalize_optional_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s if s else None


def _normalize_availability(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    s = s.lower()
    if s not in _AVAILABILITY_VALUES:
        return None
    return s


def _normalize_action_trace_level(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    if s.lower() == "none":
        return "none"
    s = s.upper()
    if s not in _ACTION_TRACE_LEVEL_VALUES:
        return None
    return s


def _normalize_action_trace_source(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    s = s.lower()
    if s not in _ACTION_TRACE_SOURCE_VALUES:
        return None
    return s


def _normalize_eval_mode(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    s = s.lower()
    if s not in _EVAL_MODE_VALUES:
        return None
    return s


def _normalize_guard_enforcement(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    s = s.lower()
    if s not in _GUARD_ENFORCEMENT_VALUES:
        return None
    return s


def _normalize_guard_unenforced_reason(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    s = s.lower()
    if s not in _GUARD_UNENFORCED_REASON_VALUES:
        return None
    return s


def _normalize_evidence_trust_level(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    s = s.lower()
    if s not in _EVIDENCE_TRUST_LEVEL_VALUES:
        return None
    return s


def _normalize_oracle_source(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    s = s.lower()
    if s not in _ORACLE_SOURCE_VALUES:
        return None
    return s


def _normalize_run_purpose(value: Any) -> Optional[str]:
    s = _normalize_optional_str(value)
    if s is None:
        return None
    s = s.lower()
    if s not in _RUN_PURPOSE_VALUES:
        return None
    return s


@dataclass(frozen=True)
class Phase0Config:
    execution_mode: str = "planner_only"
    env_profile: str = "mas_core"
    agent_name: str = "toy_agent"
    agent_provider: Optional[str] = None
    agent_model_id: Optional[str] = None
    agent_base_url: Optional[str] = None
    reset_strategy: Optional[str] = None
    snapshot_tag: Optional[str] = None
    android_serial: Optional[str] = None
    adb_path: str = "adb"
    availability: Optional[str] = None
    action_trace_level: Optional[str] = None
    action_trace_source: Optional[str] = None
    eval_mode: Optional[str] = None
    guard_enforcement: Optional[str] = None
    evidence_trust_level: Optional[str] = None
    oracle_source: Optional[str] = None
    run_purpose: Optional[str] = None


def _resolve_reset_strategy(cfg: Phase0Config) -> str:
    allowed = {"snapshot", "reinstall", "none"}
    strategy = (cfg.reset_strategy or "").strip().lower() or None
    if strategy in allowed:
        return str(strategy)
    if cfg.snapshot_tag:
        return "snapshot"
    return "none"


def probe_env_capabilities(*, repo_root: Path, cfg: Phase0Config) -> Dict[str, Any]:
    """Probe environment capabilities.

    This is best-effort and MUST NOT crash runs.
    """

    host_artifacts_root = os.environ.get("ARTIFACTS_ROOT") or os.environ.get("MAS_ARTIFACTS_ROOT")
    host_artifacts = {
        "root": host_artifacts_root,
        "exists": bool(host_artifacts_root and Path(host_artifacts_root).exists()),
    }
    if host_artifacts_root:
        p = Path(host_artifacts_root)
        host_artifacts.update(
            {
                "is_dir": p.is_dir(),
                "readable": os.access(str(p), os.R_OK),
                "writable": os.access(str(p), os.W_OK),
            }
        )

    host_artifacts_available = bool(
        host_artifacts_root
        and host_artifacts.get("exists") is True
        and host_artifacts.get("is_dir") is True
        and host_artifacts.get("readable") is True
    )
    host_artifacts["host_artifacts_available"] = host_artifacts_available

    android: Dict[str, Any] = {
        "available": False,
        "serial": cfg.android_serial,
        "adb_path": cfg.adb_path,
        "adb_version": None,
        "build_fingerprint": None,
        "android_api_level": None,
        "boot_completed": None,
        "root_available": None,
        "run_as_available": None,
        "sdcard_writable": None,
        "can_pull_data": None,
        "can_list_data_data": None,
        "notes": [],
    }

    # Only probe adb/device if a serial was provided.
    if cfg.android_serial:
        try:
            from mas_harness.runtime.android.controller import AndroidController

            controller = AndroidController(
                adb_path=cfg.adb_path,
                serial=cfg.android_serial,
                timeout_s=8.0,
            )
            android = controller.probe_env_capabilities(timeout_s=8.0)
        except Exception as e:  # pragma: no cover
            android["notes"].append(f"controller_probe_failed:{type(e).__name__}")

    capabilities = {
        "root_available": android.get("root_available"),
        "run_as_available": android.get("run_as_available"),
        "can_pull_data": android.get("can_pull_data"),
        "sdcard_writable": android.get("sdcard_writable"),
        "host_artifacts_available": host_artifacts_available,
        "android_api_level": android.get("android_api_level"),
    }

    return {
        "schema_version": "0.2",
        "created_ts_ms": _utc_ms(),
        "host": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "executable": sys.executable,
        },
        "repo": {
            "git_commit": _read_git_commit(repo_root),
        },
        "capabilities": capabilities,
        "android": android,
        "host_artifacts": host_artifacts,
    }


def build_run_manifest(*, repo_root: Path, cfg: Phase0Config, seed: int) -> Dict[str, Any]:
    """Build a run_manifest.json object.

    This records the **configuration** of the run (agent/model/provider/mode), plus
    key reproducibility anchors (git commit, adb version, fingerprint, snapshot tag).
    """

    docker_tag = os.environ.get("MAS_DOCKER_TAG") or os.environ.get("DOCKER_IMAGE_TAG")
    concurrency = _get_env_int("MAS_CONCURRENCY", 1)
    reset_strategy = _resolve_reset_strategy(cfg)
    env_profile = (cfg.env_profile or "").strip() or "mas_core"

    availability = _normalize_availability(cfg.availability)
    action_trace_level = _normalize_action_trace_level(cfg.action_trace_level) or "none"
    action_trace_source = _normalize_action_trace_source(cfg.action_trace_source)
    eval_mode = _normalize_eval_mode(cfg.eval_mode)
    guard_enforcement = _normalize_guard_enforcement(cfg.guard_enforcement)
    evidence_trust_level = _normalize_evidence_trust_level(cfg.evidence_trust_level)
    oracle_source = _normalize_oracle_source(cfg.oracle_source)
    run_purpose = _normalize_run_purpose(cfg.run_purpose)

    if availability == "audit_only":
        guard_enforcement = "unenforced"
        evidence_trust_level = "agent_reported"
        oracle_source = oracle_source or "trajectory_declared"
        run_purpose = run_purpose or "ingest_only"
    elif availability == "runnable":
        guard_enforcement = guard_enforcement or "unenforced"
        evidence_trust_level = evidence_trust_level or "tcb_captured"
        oracle_source = oracle_source or "device_query"
        run_purpose = run_purpose or "benchmark"
    else:
        evidence_trust_level = evidence_trust_level or "unknown"
        oracle_source = oracle_source or "none"
        run_purpose = run_purpose or "benchmark"

    if eval_mode is None:
        eval_mode = "guarded" if guard_enforcement == "enforced" else "vanilla"

    if action_trace_level == "none":
        action_trace_source = "none"
    else:
        action_trace_source = action_trace_source or "mas_executor"
        if action_trace_source == "none":
            action_trace_source = "mas_executor"

    # Guardrail A: only report "guard_enforced=true" when planner_only + L0.
    if eval_mode != "guarded":
        guard_enforced = False
        guard_unenforced_reason = "guard_disabled"
    elif cfg.execution_mode != "planner_only":
        guard_enforced = False
        guard_unenforced_reason = "not_planner_only"
    elif action_trace_level != "L0":
        guard_enforced = False
        guard_unenforced_reason = "not_L0"
    else:
        guard_enforced = True
        guard_unenforced_reason = None

    # Backward compatibility: keep the legacy string field as a derived value.
    guard_enforcement = "enforced" if guard_enforced else "unenforced"

    # Inference params (best-effort; adapters can override later)
    params = {
        "temperature": _get_env_float("MAS_AGENT_TEMPERATURE", 0.2),
        "top_p": _get_env_float("MAS_AGENT_TOP_P", 1.0),
        "max_tokens": _get_env_int("MAS_AGENT_MAX_TOKENS", 1024),
        "timeout_s": _get_env_int("MAS_AGENT_TIMEOUT_S", 60),
    }

    retry = {
        "max_retries": _get_env_int("MAS_AGENT_MAX_RETRIES", 2),
        "backoff_s": _get_env_int("MAS_AGENT_RETRY_BACKOFF_S", 2),
    }

    # Best-effort adb version / fingerprint (only if serial provided)
    adb_version = None
    build_fingerprint = None
    if cfg.android_serial:
        av = _try_run([cfg.adb_path, "-s", cfg.android_serial, "version"], timeout_s=5.0)
        adb_version = av.get("stdout") or av.get("stderr") or None
        fp = _try_run(
            [cfg.adb_path, "-s", cfg.android_serial, "shell", "getprop", "ro.build.fingerprint"],
            timeout_s=8.0,
        )
        if fp.get("ok"):
            build_fingerprint = fp.get("stdout") or None

    return {
        "schema_version": "0.1",
        "created_ts_ms": _utc_ms(),
        "execution_mode": cfg.execution_mode,
        "env_profile": env_profile,
        "availability": availability,
        "eval_mode": eval_mode,
        "guard_enforced": guard_enforced,
        "guard_unenforced_reason": guard_unenforced_reason,
        "action_trace_level": action_trace_level,
        "action_trace_source": action_trace_source,
        "guard_enforcement": guard_enforcement,
        "evidence_trust_level": evidence_trust_level,
        "oracle_source": oracle_source,
        "run_purpose": run_purpose,
        "seed": seed,
        "agent": {
            "agent_name": cfg.agent_name,
            "provider": cfg.agent_provider,
            "model_id": cfg.agent_model_id,
            "base_url": cfg.agent_base_url,
        },
        "inference": {
            "params": params,
            "retry": retry,
            "concurrency": concurrency,
        },
        "reproducibility": {
            "git_commit": _read_git_commit(repo_root),
            "docker_tag": docker_tag,
            "python": sys.version.split()[0],
        },
        "android": {
            "serial": cfg.android_serial,
            "adb_path": cfg.adb_path,
            "adb_version": adb_version,
            "build_fingerprint": build_fingerprint,
            # Alias to match Phase 2 Step 3 naming.
            "emulator_fingerprint": build_fingerprint,
            "snapshot_tag": cfg.snapshot_tag,
            "reset_strategy": reset_strategy,
        },
        "llm_cache": {
            "enabled": os.environ.get("MAS_LLM_CACHE", "0") in {"1", "true", "True"},
            "dir": os.environ.get("MAS_LLM_CACHE_DIR") or "llm_cache",
        },
    }


def ensure_phase0_artifacts(
    *,
    out_dir: Path,
    repo_root: Path,
    cfg: Phase0Config,
    seed: int,
) -> Dict[str, Any]:
    """Ensure run_manifest.json and env_capabilities.json exist.

    Returns a small dict suitable to embed into episode metadata.
    """

    out_dir.mkdir(parents=True, exist_ok=True)

    run_manifest_path = out_dir / "run_manifest.json"
    env_caps_path = out_dir / "env_capabilities.json"

    run_manifest = build_run_manifest(repo_root=repo_root, cfg=cfg, seed=seed)
    env_caps = probe_env_capabilities(repo_root=repo_root, cfg=cfg)

    _write_json(run_manifest_path, run_manifest)
    _write_json(env_caps_path, env_caps)

    return {
        "run_manifest": {
            "path": str(run_manifest_path.name),
            "sha256": stable_file_sha256(run_manifest_path),
        },
        "env_capabilities": {
            "path": str(env_caps_path.name),
            "sha256": stable_file_sha256(env_caps_path),
        },
        "phase0": {
            "execution_mode": cfg.execution_mode,
            "agent_name": cfg.agent_name,
        },
        "digests": {
            "run_manifest_digest": stable_sha256(run_manifest),
            "env_capabilities_digest": stable_sha256(env_caps),
        },
    }


def finalize_run_manifest_action_evidence(*, run_dir: Path) -> Dict[str, Any]:
    """Finalize action evidence fields in run_manifest.json (Phase3 3b-6d).

    Draft (run start): run_manifest contains expected action_trace_level/source.
    Finalize (run end): overwrite action_trace_level/source based on produced
    device_input_trace.jsonl files (when present and valid); otherwise degrade to
    none with an explicit reason.
    """

    run_dir = Path(run_dir)
    manifest_path = run_dir / "run_manifest.json"
    manifest = _try_read_json_object(manifest_path)
    if manifest is None:
        return {}

    expected_level = _normalize_action_trace_level(manifest.get("action_trace_level")) or "none"
    expected_source = _normalize_action_trace_source(manifest.get("action_trace_source"))
    if expected_level == "none":
        expected_source = "none"
    else:
        expected_source = expected_source or "mas_executor"
        if expected_source == "none":
            expected_source = "mas_executor"

    # Resolve per-episode evidence dirs.
    episode_dirs = _iter_episode_dirs(run_dir)

    # No episodes -> keep draft, but normalize fields.
    if not episode_dirs:
        manifest["action_trace_level"] = expected_level
        manifest["action_trace_source"] = expected_source
        manifest.pop("action_trace_degraded_from", None)
        manifest.pop("action_trace_degraded_reason", None)
        _write_json_atomic(manifest_path, manifest)
        return {
            "expected_action_trace_level": expected_level,
            "expected_action_trace_source": expected_source,
            "final_action_trace_level": expected_level,
            "final_action_trace_source": expected_source,
            "degraded": False,
            "degraded_reason": None,
        }

    # Best-effort validator import (Phase3 only).
    try:
        from mas_harness.evidence.action_evidence.device_input_trace_validator import (
            DeviceInputTraceValidationError,
            validate_device_input_trace_jsonl,
        )
    except Exception:  # pragma: no cover
        DeviceInputTraceValidationError = Exception  # type: ignore[assignment]

        def validate_device_input_trace_jsonl(*args: Any, **kwargs: Any) -> None:  # type: ignore[misc]
            return None

    any_trace_found = False
    quarantined_paths: list[Path] = []
    source_levels_found: set[str] = set()
    degrade_reason: str | None = None

    trace_paths: list[tuple[Path, Path | None]] = []
    for ep in episode_dirs:
        evidence_dir = ep / "evidence"
        root = evidence_dir if evidence_dir.is_dir() else ep
        trace_paths.append((root / "device_input_trace.jsonl", root / "screen_trace.jsonl"))

    for trace_path, screen_trace_path in trace_paths:
        if not trace_path.exists():
            continue
        any_trace_found = True
        try:
            validate_device_input_trace_jsonl(
                trace_path,
                screen_trace_path=screen_trace_path
                if screen_trace_path is not None and screen_trace_path.exists()
                else None,
            )
        except DeviceInputTraceValidationError:
            degrade_reason = degrade_reason or "invalid_device_input_trace"
            quarantined_paths.append(trace_path)
            continue

        levels = _collect_device_input_trace_source_levels(trace_path)
        if len(levels) > 1:
            degrade_reason = degrade_reason or "mixed_source_levels"
            quarantined_paths.append(trace_path)
            continue
        if len(levels) == 1:
            source_levels_found.update(levels)

    final_level: str
    if quarantined_paths:
        final_level = "none"
        for p in quarantined_paths:
            _quarantine_invalid_device_input_trace(p)
    elif not any_trace_found:
        final_level = "none"
        degrade_reason = degrade_reason or "missing_device_input_trace"
    elif len(source_levels_found) == 1:
        final_level = next(iter(source_levels_found))
    elif len(source_levels_found) > 1:
        final_level = "none"
        degrade_reason = degrade_reason or "inconsistent_source_levels"
        # Quarantine all episode traces to avoid leaving contradictory evidence behind.
        for p, _ in trace_paths:
            if p.exists():
                _quarantine_invalid_device_input_trace(p)
    else:
        # Trace file(s) exist and are valid, but contain no events -> cannot claim L0/L1/L2.
        # Degrade to "none" so run_manifest does not advertise missing action evidence.
        final_level = "none"
        degrade_reason = degrade_reason or "empty_device_input_trace"

    final_source = _action_trace_source_for_level(final_level)
    degraded = final_level == "none" and expected_level in {"L0", "L1", "L2"}

    manifest["action_trace_level"] = final_level
    manifest["action_trace_source"] = final_source
    manifest.pop("action_trace_degraded_from", None)
    manifest.pop("action_trace_degraded_reason", None)
    if degraded:
        manifest["action_trace_degraded_from"] = expected_level
        manifest["action_trace_degraded_reason"] = degrade_reason or "unknown"

    _write_json_atomic(manifest_path, manifest)
    return {
        "expected_action_trace_level": expected_level,
        "expected_action_trace_source": expected_source,
        "final_action_trace_level": final_level,
        "final_action_trace_source": final_source,
        "degraded": degraded,
        "degraded_reason": manifest.get("action_trace_degraded_reason"),
    }
