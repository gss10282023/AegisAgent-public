"""Evidence pack auditor.

This tool validates that an Evidence Pack v0 bundle is complete and readable:
- required files/dirs exist
- JSON/JSONL are parseable
- minimal key fields exist (best-effort)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from mas_harness.evidence.action_evidence.device_input_trace_validator import (
    DeviceInputTraceValidationError,
    validate_device_input_trace_jsonl,
)
from mas_harness.evidence.evidence_pack import (
    ASSERTIONS_JSONL,
    DEVICE_INPUT_TRACE_JSONL,
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS,
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON,
    EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSONL,
    EVIDENCE_PACK_V0_RUN_REQUIRED_FILES,
    FACTS_JSONL,
    evidence_pack_v0_episode_required_files_jsonl,
)
from mas_harness.oracle_framework.schema_validators import (
    assert_assertion_result_v0,
    assert_fact_v0,
)
from mas_harness.oracles.zoo.base import assert_oracle_event_v0

_ALLOWED_INCONCLUSIVE_REASONS_V0: set[str] = {
    "missing_policy",
    "missing_fact",
    "missing_effect_evidence",
    "missing_package_diff_evidence",
    "missing_settings_diff_evidence",
    "missing_success_oracle_name",
    "missing_oracle_decision",
    "oracle_inconclusive",
    "policy_missing_budget",
    "missing_canary_or_sinks",
    "missing_consent_trace",
    "missing_binding_state",
    "not_applicable",
    "invalid_assertion_config",
    "unknown_assertion_id",
    "assertion_runtime_error",
}

_ALLOWED_INCONCLUSIVE_REASON_PREFIXES_V0: tuple[str, ...] = ("assertion_error:",)


def _find_run_root(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for _ in range(20):
        if all((cur / name).exists() for name in EVIDENCE_PACK_V0_RUN_REQUIRED_FILES):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _load_action_trace_level(run_root: Path) -> Optional[str]:
    try:
        obj = _load_json(run_root / "run_manifest.json")
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    value = obj.get("action_trace_level")
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _iter_episode_dirs(root: Path) -> List[Path]:
    summaries = sorted(root.glob("**/summary.json"))
    dirs: set[Path] = set()
    for p in summaries:
        if not p.is_file():
            continue
        d = p.parent
        # Phase-3 layout: <episode_dir>/evidence/summary.json
        if d.name == "evidence":
            dirs.add(d.parent)
            continue
        # Phase-3 layout: <episode_dir>/summary.json + <episode_dir>/evidence/*
        if (d / "evidence" / "summary.json").is_file():
            dirs.add(d)
            continue
        # Phase-2 layout: evidence files live directly inside the episode dir.
        dirs.add(d)
    return sorted(dirs)


def _is_evidence_pack_episode_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not (path / "summary.json").is_file():
        return False
    if not all((path / d).is_dir() for d in EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS):
        return False
    if not all((path / f).is_file() for f in EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSONL):
        return False
    if not all((path / f).is_file() for f in EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON):
        return False
    return True


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _audit_json(path: Path, *, required_keys: Sequence[str]) -> List[str]:
    errors: List[str] = []
    if not path.exists():
        return [f"missing file: {path}"]
    try:
        obj = _load_json(path)
    except Exception as e:
        return [f"invalid json: {path} ({e})"]
    if not isinstance(obj, dict):
        errors.append(f"json must be an object: {path}")
        return errors
    for k in required_keys:
        if k not in obj:
            errors.append(f"missing key '{k}' in {path}")
    return errors


def _audit_jsonl(
    path: Path,
    *,
    required_keys_for_events: Mapping[str, Sequence[str]] | None = None,
    validate_event: Callable[[Mapping[str, Any]], None] | None = None,
) -> List[str]:
    errors: List[str] = []
    if not path.exists():
        return [f"missing file: {path}"]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return [f"unreadable file: {path} ({e})"]

    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception as e:
            errors.append(f"invalid jsonl: {path}:{i} ({e})")
            continue
        if not isinstance(obj, dict):
            errors.append(f"jsonl line must be an object: {path}:{i}")
            continue

        event = obj.get("event")
        if not isinstance(event, str) or not event:
            errors.append(f"missing event field: {path}:{i}")
            continue

        if "ts_ms" not in obj:
            errors.append(f"missing ts_ms field: {path}:{i}")

        if required_keys_for_events and event in required_keys_for_events:
            for k in required_keys_for_events[event]:
                if k not in obj:
                    errors.append(f"missing key '{k}' for event={event}: {path}:{i}")

        if validate_event is not None:
            try:
                validate_event(obj)
            except Exception as e:
                errors.append(f"event validation failed: {path}:{i} ({e})")

    return errors


def _audit_schema_jsonl(
    path: Path,
    *,
    validate_obj: Callable[[Mapping[str, Any]], None],
    label: str,
) -> List[str]:
    errors: List[str] = []
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return [f"unreadable file: {path} ({e})"]

    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception as e:
            errors.append(f"invalid jsonl: {path}:{i} ({e})")
            continue
        if not isinstance(obj, dict):
            errors.append(f"jsonl line must be an object: {path}:{i}")
            continue
        try:
            validate_obj(obj)
        except Exception as e:
            errors.append(f"{label} validation failed: {path}:{i} ({e})")

    return errors


def _audit_assertion_inconclusive_reasons(path: Path) -> List[str]:
    if not path.exists():
        return []

    errors: List[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return [f"unreadable file: {path} ({e})"]

    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue

        if obj.get("result") != "INCONCLUSIVE":
            continue

        reason = obj.get("inconclusive_reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"inconclusive_reason is required for result=INCONCLUSIVE: {path}:{i}")
            continue

        reason = reason.strip()
        if reason in _ALLOWED_INCONCLUSIVE_REASONS_V0:
            continue
        if any(reason.startswith(prefix) for prefix in _ALLOWED_INCONCLUSIVE_REASON_PREFIXES_V0):
            continue
        errors.append(f"invalid inconclusive_reason (unexpected enum): {path}:{i} ({reason!r})")

    return errors


def audit_device_input_trace(
    path: Path,
    *,
    expected_source_level: str | None = None,
    screen_trace_path: Path | None = None,
) -> List[str]:
    errors: List[str] = []
    expected_level = str(expected_source_level or "").strip().upper()
    require_file = expected_level in {"L0", "L1", "L2"}
    if not path.exists():
        return [f"missing file: {path}"] if require_file else []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return [f"unreadable file: {path} ({e})"]

    if require_file and not any(line.strip() for line in lines):
        return [f"empty device_input_trace.jsonl: {path}"]

    enforce_expected_level = expected_level in {"L0", "L1", "L2"}
    last_step_idx: int | None = None
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception as e:
            errors.append(f"invalid jsonl: {path}:{i} ({e})")
            continue
        if not isinstance(obj, dict):
            errors.append(f"jsonl line must be an object: {path}:{i}")
            continue

        def require_key(key: str) -> Any | None:
            if key not in obj:
                errors.append(f"missing key '{key}': {path}:{i}")
                return None
            return obj.get(key)

        step_idx = require_key("step_idx")
        ref_step_idx = obj.get("ref_step_idx")
        source_level = require_key("source_level")
        event_type = require_key("event_type")
        payload = require_key("payload")
        timestamp_ms = require_key("timestamp_ms")
        mapping_warnings = require_key("mapping_warnings")

        if (
            enforce_expected_level
            and isinstance(source_level, str)
            and source_level != expected_level
        ):
            errors.append(f"source_level mismatch (expected {expected_level}): {path}:{i}")

        if isinstance(step_idx, bool) or not isinstance(step_idx, int):
            errors.append(f"step_idx must be int: {path}:{i}")
        else:
            if last_step_idx is not None and step_idx <= last_step_idx:
                errors.append(f"step_idx must be strictly increasing: {path}:{i}")
            last_step_idx = int(step_idx)

        if ref_step_idx is not None and (
            isinstance(ref_step_idx, bool) or not isinstance(ref_step_idx, int)
        ):
            errors.append(f"ref_step_idx must be int|null: {path}:{i}")

        if not isinstance(source_level, str) or source_level not in {"L0", "L1", "L2"}:
            errors.append(f"source_level must be one of L0|L1|L2: {path}:{i}")

        if not isinstance(event_type, str) or not event_type.strip():
            errors.append(f"event_type must be a non-empty string: {path}:{i}")

        if not isinstance(payload, dict):
            errors.append(f"payload must be an object: {path}:{i}")

        if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
            errors.append(f"timestamp_ms must be int: {path}:{i}")

        if not isinstance(mapping_warnings, list) or not all(
            isinstance(w, str) for w in mapping_warnings
        ):
            errors.append(f"mapping_warnings must be list[str]: {path}:{i}")

        if isinstance(source_level, str) and source_level == "L0":
            if not isinstance(ref_step_idx, int):
                errors.append(f"ref_step_idx is required for L0: {path}:{i}")
            elif isinstance(step_idx, int) and ref_step_idx != step_idx:
                errors.append(f"ref_step_idx must equal step_idx for L0: {path}:{i}")

    try:
        validate_device_input_trace_jsonl(path, screen_trace_path=screen_trace_path)
    except DeviceInputTraceValidationError as e:
        for line in str(e).splitlines():
            errors.append(line)

    return errors


def _collect_event_steps(path: Path, *, event: str, step_keys: Sequence[str]) -> List[int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    steps: List[int] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict) or obj.get("event") != event:
            continue
        for k in step_keys:
            v = obj.get(k)
            try:
                if isinstance(v, bool) or v is None:
                    continue
                steps.append(int(v))
                break
            except Exception:
                continue
    return steps


def audit_episode_dir(episode_dir: Path, *, action_trace_level: str | None = None) -> List[str]:
    """Audit a single episode bundle directory."""

    errors: List[str] = []
    episode_dir = episode_dir.resolve()
    if not episode_dir.is_dir():
        return [f"not a directory: {episode_dir}"]

    # Phase-3 layout: evidence lives under episode_dir/evidence/.
    evidence_dir = episode_dir / "evidence"
    if _is_evidence_pack_episode_dir(evidence_dir):
        episode_dir = evidence_dir

    # Required dirs
    for d in EVIDENCE_PACK_V0_EPISODE_REQUIRED_DIRS:
        p = episode_dir / d
        if not p.exists():
            errors.append(f"missing dir: {p}")
        elif not p.is_dir():
            errors.append(f"expected dir, found file: {p}")

    # Step 7: require at least one UIAutomator XML dump per episode.
    ui_dump_dir = episode_dir / "ui_dump"
    if ui_dump_dir.exists() and ui_dump_dir.is_dir():
        if not any(ui_dump_dir.glob("uiautomator_*.xml")):
            errors.append(f"missing UIAutomator XML in: {ui_dump_dir} (expected uiautomator_*.xml)")

    # Required files
    required_jsonl_files = evidence_pack_v0_episode_required_files_jsonl(
        action_trace_level=action_trace_level
    )
    for name in required_jsonl_files:
        p = episode_dir / name
        if name == DEVICE_INPUT_TRACE_JSONL:
            continue
        required_keys: Dict[str, Sequence[str]] = {}

        if name == "obs_trace.jsonl":
            required_keys = {
                "observation": ["step", "ui_hash", "screenshot_file", "screenshot_sha256"],
            }
        elif name == "foreground_trace.jsonl":
            required_keys = {"foreground": ["step"]}
        elif name == "device_trace.jsonl":
            required_keys = {"device": ["step"]}
        elif name == "screen_trace.jsonl":
            required_keys = {
                "screen": [
                    "step",
                    "width_px",
                    "height_px",
                    "density_dpi",
                    "surface_orientation",
                    "screenshot_size_px",
                    "logical_screen_size_px",
                    "physical_frame_boundary_px",
                    "orientation",
                ]
            }
        elif name == "action_trace.jsonl":
            required_keys = {"action": ["step", "action", "result"]}
        elif name == "agent_action_trace.jsonl":
            required_keys = {
                "agent_action": [
                    "step_idx",
                    "raw_action",
                    "normalized_action",
                    "normalization_warnings",
                ]
            }
        elif name == "agent_call_trace.jsonl":
            required_keys = {
                "agent_call": [
                    "step_idx",
                    "agent_name",
                    "provider",
                    "model_id",
                    "base_url",
                    "input_digest",
                    "response_digest",
                    "latency_ms",
                    "tokens_in",
                    "tokens_out",
                    "error",
                ]
            }
        elif name == "ui_elements.jsonl":
            required_keys = {
                "ui_elements": ["step", "ui_hash", "source", "ui_elements", "elements_count"]
            }

        validate_event = assert_oracle_event_v0 if name == "oracle_trace.jsonl" else None
        errors.extend(
            _audit_jsonl(
                p,
                required_keys_for_events=required_keys or None,
                validate_event=validate_event,
            )
        )

        # Step 7: validate ui_elements payload shape (best-effort).
        if name == "ui_elements.jsonl" and p.exists():
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
                ui_events = []
                for line in lines:
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if obj.get("event") == "ui_elements":
                        ui_events.append(obj)
            except Exception as e:
                errors.append(f"invalid jsonl: {p} ({e})")
            else:
                if not ui_events:
                    errors.append(f"missing ui_elements events in: {p}")
                else:
                    for i, ev in enumerate(ui_events, start=1):
                        elems = ev.get("ui_elements")
                        if not isinstance(elems, list) or not elems:
                            errors.append(f"ui_elements must be a non-empty list: {p} (event #{i})")
                            continue
                        for j, el in enumerate(elems, start=1):
                            if not isinstance(el, dict):
                                errors.append(
                                    f"ui_elements[{j}] must be an object: {p} (event #{i})"
                                )
                                continue
                            bbox = el.get("bbox")
                            if (
                                not isinstance(bbox, list)
                                or len(bbox) != 4
                                or not all(isinstance(x, int) for x in bbox)
                            ):
                                errors.append(
                                    f"ui_elements[{j}].bbox must be [int,int,int,int]: {p} "
                                    f"(event #{i})"
                                )
                            if not isinstance(el.get("clickable"), bool):
                                errors.append(
                                    f"ui_elements[{j}].clickable must be bool: {p} (event #{i})"
                                )
                            for k in ("enabled", "focused", "selected", "checked", "scrollable"):
                                if (
                                    k in el
                                    and el.get(k) is not None
                                    and not isinstance(el.get(k), bool)
                                ):
                                    errors.append(
                                        f"ui_elements[{j}].{k} must be bool|null: {p} (event #{i})"
                                    )
                            if not isinstance(el.get("package"), str):
                                errors.append(
                                    f"ui_elements[{j}].package must be str: {p} (event #{i})"
                                )
                            text = el.get("text")
                            desc = el.get("desc")
                            rid = el.get("resource_id")
                            if not any(isinstance(v, str) and v.strip() for v in (text, desc, rid)):
                                errors.append(
                                    f"ui_elements[{j}] must have text|desc|resource_id: {p} "
                                    f"(event #{i})"
                                )

    errors.extend(
        audit_device_input_trace(
            episode_dir / DEVICE_INPUT_TRACE_JSONL,
            expected_source_level=action_trace_level,
            screen_trace_path=episode_dir / "screen_trace.jsonl",
        )
    )

    # JSON files
    for name in EVIDENCE_PACK_V0_EPISODE_REQUIRED_FILES_JSON:
        p = episode_dir / name
        if name == "summary.json":
            errors.extend(
                _audit_json(
                    p,
                    required_keys=["case_id", "seed", "status", "task_success"],
                )
            )
        else:
            errors.extend(_audit_json(p, required_keys=[]))

    # Phase4: optional audit-first artifacts.
    errors.extend(
        _audit_schema_jsonl(
            episode_dir / FACTS_JSONL,
            validate_obj=assert_fact_v0,
            label="fact",
        )
    )
    errors.extend(
        _audit_schema_jsonl(
            episode_dir / ASSERTIONS_JSONL,
            validate_obj=assert_assertion_result_v0,
            label="assertion_result",
        )
    )
    errors.extend(_audit_assertion_inconclusive_reasons(episode_dir / ASSERTIONS_JSONL))

    # Step 14: agent traces must exist per step (best-effort).
    obs_steps = set(
        _collect_event_steps(
            episode_dir / "obs_trace.jsonl",
            event="observation",
            step_keys=["step"],
        )
    )
    agent_call_steps = set(
        _collect_event_steps(
            episode_dir / "agent_call_trace.jsonl",
            event="agent_call",
            step_keys=["step_idx", "step"],
        )
    )
    agent_action_steps = set(
        _collect_event_steps(
            episode_dir / "agent_action_trace.jsonl",
            event="agent_action",
            step_keys=["step_idx", "step"],
        )
    )
    if obs_steps and not agent_call_steps:
        errors.append(f"missing agent_call events in: {episode_dir / 'agent_call_trace.jsonl'}")
    if obs_steps and not agent_action_steps:
        errors.append(f"missing agent_action events in: {episode_dir / 'agent_action_trace.jsonl'}")

    missing_calls = sorted(obs_steps - agent_call_steps)
    if missing_calls:
        errors.append(f"agent_call_trace missing step_idx: {missing_calls}")
    missing_actions = sorted(obs_steps - agent_action_steps)
    if missing_actions:
        errors.append(f"agent_action_trace missing step_idx: {missing_actions}")

    return errors


def audit_run_root(run_root: Path) -> List[str]:
    """Audit a run root containing run-level artifacts and 1..N episode bundles."""

    errors: List[str] = []
    run_root = run_root.resolve()
    if not run_root.is_dir():
        return [f"not a directory: {run_root}"]

    # Run-level JSON files (Phase-0 governance artifacts).
    run_manifest = run_root / "run_manifest.json"
    env_caps = run_root / "env_capabilities.json"
    errors.extend(
        _audit_json(
            run_manifest,
            required_keys=[
                "schema_version",
                "created_ts_ms",
                "execution_mode",
                "seed",
                "agent",
                "inference",
                "reproducibility",
                "android",
                "llm_cache",
            ],
        )
    )
    errors.extend(
        _audit_json(
            env_caps,
            required_keys=[
                "schema_version",
                "created_ts_ms",
                "host",
                "repo",
                "android",
                "host_artifacts",
            ],
        )
    )

    action_trace_level = _load_action_trace_level(run_root)
    episode_dirs = _iter_episode_dirs(run_root)
    if not episode_dirs:
        errors.append(f"no episode bundles found under: {run_root}")
        return errors

    for ep in episode_dirs:
        errors.extend(audit_episode_dir(ep, action_trace_level=action_trace_level))
    return errors


def audit_bundle(path: Path) -> List[str]:
    """Audit either a run root or a single episode dir.

    - If `path` (or an ancestor) contains run-level artifacts, audits those.
    - If `path` contains summary.json, audits that single episode.
    - Otherwise, treats `path` as a run root and audits discovered episodes.
    """

    path = path.resolve()
    if (path / "summary.json").exists():
        run_root = _find_run_root(path)
        action_trace_level = _load_action_trace_level(run_root) if run_root is not None else None
        errors = audit_episode_dir(path, action_trace_level=action_trace_level)
        if run_root is not None:
            errors.extend(audit_run_root(run_root))
        return errors

    run_root = _find_run_root(path) or path
    return audit_run_root(run_root)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit MAS Evidence Pack v0 bundles.")
    parser.add_argument(
        "path",
        type=Path,
        help="Run root (contains run_manifest.json) or episode dir",
    )
    args = parser.parse_args(argv)

    errors = audit_bundle(args.path)
    if errors:
        for e in errors:
            print(f"[ERR] {e}")
        return 2
    print("[OK] bundle audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
