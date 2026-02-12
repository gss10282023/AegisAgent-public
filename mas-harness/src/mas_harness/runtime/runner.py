from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from mas_harness.evidence import EvidenceWriter, stable_sha256
from mas_harness.evidence.action_evidence.base import parse_action_evidence_spec
from mas_harness.evidence.action_evidence.materialize import (
    ActionEvidenceMaterializationError,
    materialize_action_evidence,
)
from mas_harness.integration.agents.adapter_manifest import load_adapter_manifest
from mas_harness.integration.agents.base import AdapterRunContext, AgentAdapterError
from mas_harness.integration.agents.loader import load_agent_adapter
from mas_harness.integration.agents.registry import (
    validate_registry_manifest_consistency,
)
from mas_harness.oracle_framework.audit_engine import AuditEngine
from mas_harness.phases.phase0_artifacts import (
    Phase0Config,
    finalize_run_manifest_action_evidence,
)
from mas_harness.spec import load_and_validate_case
from mas_harness.spec.validate_specs import iter_case_dirs
from mas_harness.tools.audit_bundle import audit_episode_dir


def _extract_initial_url(task_spec: Mapping[str, Any]) -> str | None:
    initial_state = task_spec.get("initial_state")
    if not isinstance(initial_state, Mapping):
        return None
    url = initial_state.get("url") or initial_state.get("uri")
    return url.strip() if isinstance(url, str) and url.strip() else None


def _needs_case_site_server(task_spec: Mapping[str, Any]) -> bool:
    url = _extract_initial_url(task_spec)
    if not url:
        return False
    return "${MAS_CASE_SITE_PORT}" in url or "${MAS_CASE_SITE_HOST}" in url


def _split_csv(value: str) -> list[str]:
    out: list[str] = []
    for raw in value.split(","):
        s = raw.strip()
        if s:
            out.append(s)
    return out


def _precase_uninstall_packages() -> list[str]:
    raw = os.environ.get("MAS_PRECASE_UNINSTALL_PACKAGES")
    if raw is None:
        # Default: keep the environment free of the "friendly" compromise-canary APK.
        return ["com.friendlyapps.notepad"]
    raw = raw.strip()
    if not raw or raw.lower() in {"none", "off"}:
        return []
    return _split_csv(raw)


def _adb_run(
    *,
    adb_path: str,
    serial: str,
    args: list[str],
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    cmd = [str(adb_path)]
    serial = str(serial).strip()
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def _adb_is_package_installed(
    *,
    adb_path: str,
    serial: str,
    package: str,
    timeout_s: float,
) -> bool:
    res = _adb_run(
        adb_path=adb_path,
        serial=serial,
        args=["shell", "pm", "path", package],
        timeout_s=timeout_s,
    )
    return res.returncode == 0 and bool((res.stdout or "").strip())


def _precase_uninstall_packages_best_effort(
    *,
    adb_path: str,
    serial: str,
    packages: list[str],
    out_path: Path,
    timeout_s: float = 15.0,
) -> None:
    results: list[dict[str, Any]] = []

    # Best-effort wait so we don't fail due to transient "device offline" after snapshot loads.
    try:
        _adb_run(
            adb_path=adb_path,
            serial=serial,
            args=["wait-for-device"],
            timeout_s=timeout_s,
        )
    except Exception as e:
        results.append({"kind": "wait_for_device_error", "error": repr(e)})

    for package in packages:
        entry: dict[str, Any] = {"package": package}
        try:
            installed = _adb_is_package_installed(
                adb_path=adb_path,
                serial=serial,
                package=package,
                timeout_s=timeout_s,
            )
            entry["installed_before"] = installed
            if installed:
                res = _adb_run(
                    adb_path=adb_path,
                    serial=serial,
                    args=["uninstall", package],
                    timeout_s=timeout_s,
                )
                entry.update(
                    {
                        "uninstall_returncode": res.returncode,
                        "uninstall_stdout": (res.stdout or "").strip(),
                        "uninstall_stderr": (res.stderr or "").strip(),
                    }
                )
            else:
                entry["skipped_reason"] = "not_installed"
        except Exception as e:
            entry["error"] = repr(e)
        results.append(entry)

    out_path.write_text(_json_dumps_canonical({"results": results}) + "\n", encoding="utf-8")


def _extract_harness_cleanup(task_spec: Mapping[str, Any]) -> dict[str, list[str]]:
    cleanup = task_spec.get("harness_cleanup")
    if not isinstance(cleanup, Mapping):
        return {}

    uninstall_raw = cleanup.get("uninstall_packages")
    remove_raw = cleanup.get("remove_sdcard_paths")

    uninstall = [str(p).strip() for p in uninstall_raw] if isinstance(uninstall_raw, list) else []
    remove = [str(p).strip() for p in remove_raw] if isinstance(remove_raw, list) else []

    uninstall = [p for p in uninstall if p]
    remove = [p for p in remove if p]

    out: dict[str, list[str]] = {}
    if uninstall:
        out["uninstall_packages"] = uninstall
    if remove:
        out["remove_sdcard_paths"] = remove
    return out


def _apply_harness_cleanup(
    *,
    phase0_cfg: Phase0Config,
    cleanup: Mapping[str, list[str]] | None,
    timeout_s: float = 30.0,
) -> None:
    serial = str(getattr(phase0_cfg, "android_serial", "") or "").strip()
    if not serial:
        return
    if not cleanup:
        return

    adb_path = str(getattr(phase0_cfg, "adb_path", "adb"))

    for pkg in cleanup.get("uninstall_packages", []):
        pkg_str = str(pkg).strip()
        if not pkg_str:
            continue
        _adb_run(
            adb_path=adb_path,
            serial=serial,
            args=["uninstall", pkg_str],
            timeout_s=timeout_s,
        )

    for path in cleanup.get("remove_sdcard_paths", []):
        path_str = str(path).strip()
        if not path_str:
            continue
        _adb_run(
            adb_path=adb_path,
            serial=serial,
            args=["shell", "rm", "-f", path_str],
            timeout_s=timeout_s,
        )


@contextmanager
def _maybe_start_case_site_server(
    *,
    case_dir: Path,
    phase0_cfg: Phase0Config,
    task_spec: Mapping[str, Any],
) -> Iterator[dict[str, str]]:
    if not _needs_case_site_server(task_spec):
        yield {}
        return

    site_dir = (case_dir.parent / "site").resolve()
    if not site_dir.is_dir():
        raise AgentAdapterError(f"case site directory missing: {site_dir}")

    path_map = {
        "/help_center_contact_v1.html": "/benign.html",
        "/help_center_contact_v2.html": "/attack.html",
        "/": "/benign.html",
    }

    apk_file: Path | None = None
    apk_bytes: bytes | None = None
    apk_content_type = "application/vnd.android.package-archive"

    apk_candidate = (site_dir / "acme_support_inbox.apk").resolve()
    if apk_candidate.is_file():
        apk_file = apk_candidate
    else:
        b64_candidate = site_dir / "acme_support_inbox.apk.b64"
        if b64_candidate.is_file():
            try:
                raw = b64_candidate.read_text(encoding="utf-8", errors="strict")
                apk_bytes = base64.b64decode(raw.encode("utf-8"))
            except Exception:
                apk_bytes = None

    class _Handler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def end_headers(self) -> None:
            # Avoid Android Chrome caching the local case-site content between runs/cases,
            # since many cases share the same stable URLs (e.g., /help_center_contact_v1.html).
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

        def _serve_binary_file(self, path: Path, *, content_type: str, send_body: bool) -> None:
            try:
                size = int(path.stat().st_size)
            except Exception:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if not send_body:
                return
            try:
                data = path.read_bytes()
            except Exception:
                self.send_error(404)
                return
            self.wfile.write(data)

        def _serve_bytes(self, data: bytes, *, content_type: str, send_body: bool) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not send_body:
                return
            self.wfile.write(data)

        def _maybe_map_path(self) -> None:
            raw_path = str(self.path or "")
            path_only = raw_path.split("?", 1)[0].split("#", 1)[0]
            mapped = path_map.get(path_only)
            if mapped is None:
                return
            if not (site_dir / mapped.lstrip("/")).is_file():
                return
            self.path = mapped + ("" if "?" not in raw_path else "?" + raw_path.split("?", 1)[1])

        def do_GET(self) -> None:  # noqa: N802
            raw_path = str(self.path or "")
            path_only = raw_path.split("?", 1)[0].split("#", 1)[0]
            if path_only == "/acme_support_inbox.apk":
                if apk_file is not None:
                    self._serve_binary_file(apk_file, content_type=apk_content_type, send_body=True)
                    return
                if apk_bytes is not None:
                    self._serve_bytes(apk_bytes, content_type=apk_content_type, send_body=True)
                    return
            self._maybe_map_path()
            super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802
            raw_path = str(self.path or "")
            path_only = raw_path.split("?", 1)[0].split("#", 1)[0]
            if path_only == "/acme_support_inbox.apk":
                if apk_file is not None:
                    self._serve_binary_file(
                        apk_file,
                        content_type=apk_content_type,
                        send_body=False,
                    )
                    return
                if apk_bytes is not None:
                    self._serve_bytes(apk_bytes, content_type=apk_content_type, send_body=False)
                    return
            self._maybe_map_path()
            super().do_HEAD()

    handler_factory = partial(_Handler, directory=str(site_dir))
    bind_host = str(os.environ.get("MAS_CASE_SITE_BIND_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    bind_port = 0
    bind_port_raw = str(os.environ.get("MAS_CASE_SITE_SERVER_PORT") or "").strip()
    if bind_port_raw:
        try:
            bind_port = int(bind_port_raw)
        except Exception as e:
            raise AgentAdapterError(f"Invalid MAS_CASE_SITE_SERVER_PORT: {bind_port_raw!r}") from e
        if bind_port <= 0 or bind_port > 65535:
            raise AgentAdapterError(f"Invalid MAS_CASE_SITE_SERVER_PORT: {bind_port_raw!r}")

    server = ThreadingHTTPServer((bind_host, bind_port), handler_factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host_for_device = "127.0.0.1"
        port = int(server.server_address[1])

        serial = str(getattr(phase0_cfg, "android_serial", "") or "").strip()
        adb_path = str(getattr(phase0_cfg, "adb_path", "adb"))

        # For emulators, 10.0.2.2 maps to host loopback and avoids adb reverse.
        if serial.startswith("emulator-"):
            host_for_device = "10.0.2.2"
        elif serial:
            res = _adb_run(
                adb_path=adb_path,
                serial=serial,
                args=["reverse", f"tcp:{port}", f"tcp:{port}"],
                timeout_s=15.0,
            )
            if res.returncode != 0:
                raise AgentAdapterError(
                    "adb reverse failed; cannot expose host case site to device\n"
                    f"cmd={' '.join(res.args)}\nstdout={(res.stdout or '').strip()}\n"
                    f"stderr={(res.stderr or '').strip()}"
                )

        yield {"MAS_CASE_SITE_HOST": host_for_device, "MAS_CASE_SITE_PORT": str(port)}
    finally:
        try:
            serial = str(getattr(phase0_cfg, "android_serial", "") or "").strip()
            adb_path = str(getattr(phase0_cfg, "adb_path", "adb"))
            port = int(server.server_address[1])
            if serial and not serial.startswith("emulator-"):
                _adb_run(
                    adb_path=adb_path,
                    serial=serial,
                    args=["reverse", "--remove", f"tcp:{port}"],
                    timeout_s=10.0,
                )
        except Exception:
            pass

        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass

        try:
            thread.join(timeout=2.0)
        except Exception:
            pass


def _resolve_path(repo_root: Path, raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (repo_root / p).resolve()


def _find_registry_entry(
    entries: Iterable[Dict[str, Any]],
    agent_id: str,
) -> Optional[Dict[str, Any]]:
    needle = agent_id.strip()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("agent_id", "")).strip() == needle:
            return entry
    return None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _try_load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(_json_dumps_canonical(obj), encoding="utf-8")
    tmp_path.replace(path)


def _patch_episode_summaries_action_evidence(
    *,
    episode_dir: Path,
    action_trace_level: str,
    action_trace_source: str,
    strict_failure: bool,
    strict_failure_class: str,
    strict_failure_reason: str,
) -> None:
    for summary_path in (
        episode_dir / "summary.json",
        episode_dir / "evidence" / "summary.json",
    ):
        if not summary_path.exists():
            continue
        obj = _try_load_json_object(summary_path)
        if obj is None:
            continue
        obj["action_trace_level"] = action_trace_level
        obj["action_trace_source"] = action_trace_source
        if strict_failure:
            obj["status"] = "inconclusive"
            obj["terminated_reason"] = "strict_action_evidence"
            obj["failure_class"] = strict_failure_class
            obj["oracle_decision"] = "inconclusive"
            obj["task_success"] = "unknown"
            notes = obj.get("notes")
            if not isinstance(notes, dict):
                notes = {}
                obj["notes"] = notes
            notes["strict_action_evidence"] = {
                "reason": strict_failure_reason,
                "failure_class": strict_failure_class,
            }
        _write_json_atomic(summary_path, obj)


@contextmanager
def _temporary_environ(overrides: Mapping[str, str | None]) -> Iterator[None]:
    old = {k: os.environ.get(k) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _ensure_episode_layout(*, episode_dir: Path) -> Path:
    episode_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = episode_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return evidence_dir


def _copy_episode_summary(*, evidence_dir: Path, episode_dir: Path) -> None:
    src = evidence_dir / "summary.json"
    dst = episode_dir / "summary.json"
    if not src.exists():
        return
    _write_text(dst, src.read_text(encoding="utf-8"))


def _audit_evidence_dir(evidence_dir: Path, *, action_trace_level: str | None = None) -> None:
    errors = audit_episode_dir(evidence_dir, action_trace_level=action_trace_level)
    if errors:
        raise AgentAdapterError("evidence audit failed:\n" + "\n".join(errors))


def _load_adapter_manifest(adapter_path: Path) -> Optional[Dict[str, Any]]:
    manifest_path = adapter_path.parent / "adapter_manifest.json"
    if not manifest_path.exists():
        return None
    return load_adapter_manifest(manifest_path)


def _infer_action_trace_source(
    *,
    repo_root: Path,
    registry_entry: Dict[str, Any],
    availability: str,
    action_trace_level: str | None,
) -> str | None:
    if availability != "runnable":
        return None

    level = str(action_trace_level or "").strip().upper()
    if level == "L0":
        return "mas_executor"
    if level not in {"L1", "L2"}:
        return None

    adapter_raw = registry_entry.get("adapter")
    if not isinstance(adapter_raw, str) or not adapter_raw.strip():
        return None

    adapter_path = _resolve_path(repo_root, adapter_raw.strip())
    manifest = _load_adapter_manifest(adapter_path)
    if manifest is None:
        return None

    try:
        spec = parse_action_evidence_spec(manifest)
    except Exception as e:
        raise AgentAdapterError(str(e)) from e
    if spec is None:
        return None

    return spec.source.strip().lower() or None


def _run_runnable(
    *,
    agent_id: str,
    registry_entry: Dict[str, Any],
    case_dir: Path,
    output: Path,
    seed: int,
    repo_root: Path,
    schemas_dir: Path,
    phase0_cfg: Phase0Config,
    run_metadata: Dict[str, Any],
    dry_run_ingest_events: Path | None,
    comm_proxy_mode: str = "off",
    strict_action_evidence: bool = False,
) -> None:
    adapter_raw = registry_entry.get("adapter")
    if not isinstance(adapter_raw, str) or not adapter_raw.strip():
        raise AgentAdapterError(f"runnable agent missing adapter path in registry: {agent_id}")
    adapter_path = _resolve_path(repo_root, adapter_raw.strip())

    consistency = validate_registry_manifest_consistency([registry_entry], repo_root=repo_root)
    if consistency.errors:
        raise AgentAdapterError(
            "registry ↔ adapter_manifest consistency check failed:\n"
            + "\n".join(i.format() for i in consistency.errors)
        )

    adapter = load_agent_adapter(adapter_path)

    adapter_manifest = _load_adapter_manifest(adapter_path)
    try:
        action_evidence_spec = (
            parse_action_evidence_spec(adapter_manifest) if adapter_manifest is not None else None
        )
    except Exception as e:
        raise AgentAdapterError(str(e)) from e

    use_l2_comm_proxy = bool(
        action_evidence_spec is not None
        and action_evidence_spec.level.strip().upper() == "L2"
        and action_evidence_spec.source.strip().lower() == "comm_proxy"
        and str(comm_proxy_mode).strip().lower() == "record"
    )

    ctx = AdapterRunContext(
        repo_root=repo_root,
        schemas_dir=schemas_dir,
        seed=seed,
        phase0_cfg=phase0_cfg,
        run_metadata=run_metadata,
        registry_entry=registry_entry,
        output_dir=output,
    )

    episode_idx = 0
    episode_evidence_dirs: list[tuple[Path, Path]] = []
    for one_case_dir in iter_case_dirs(case_dir):
        episode_dir = output / f"episode_{episode_idx:04d}"
        evidence_dir = _ensure_episode_layout(episode_dir=episode_dir)
        packages = _precase_uninstall_packages()
        if packages and phase0_cfg.android_serial:
            _precase_uninstall_packages_best_effort(
                adb_path=str(phase0_cfg.adb_path or "adb"),
                serial=str(phase0_cfg.android_serial),
                packages=packages,
                out_path=evidence_dir / "precase_uninstall_packages.json",
            )
        case_specs = load_and_validate_case(case_dir=one_case_dir, schemas_dir=schemas_dir)
        env_overrides: dict[str, str | None] = {}
        cleanup_cfg = _extract_harness_cleanup(case_specs.task)

        try:
            with _maybe_start_case_site_server(
                case_dir=one_case_dir,
                phase0_cfg=phase0_cfg,
                task_spec=case_specs.task,
            ) as site_overrides:
                env_overrides.update(site_overrides)

                if use_l2_comm_proxy:
                    trace_path = evidence_dir / "comm_proxy_trace.jsonl"
                    trace_path.write_text("", encoding="utf-8")

                    from mas_harness.evidence.action_evidence.l2_http_recorder import (
                        HttpJsonActionRecorder,
                    )

                    recorder = HttpJsonActionRecorder(trace_path)
                    recorder.start()
                    try:
                        env_overrides.update(
                            {
                                "MAS_COMM_PROXY_BASE_URL": recorder.base_url,
                                "MAS_COMM_PROXY_ACT_PATH": "/act",
                            }
                        )
                        with _temporary_environ(env_overrides):
                            _apply_harness_cleanup(phase0_cfg=phase0_cfg, cleanup=cleanup_cfg)
                            adapter.run_case(
                                case_dir=one_case_dir, evidence_dir=evidence_dir, ctx=ctx
                            )
                    finally:
                        recorder.stop()
                else:
                    with _temporary_environ(env_overrides):
                        _apply_harness_cleanup(phase0_cfg=phase0_cfg, cleanup=cleanup_cfg)
                        adapter.run_case(case_dir=one_case_dir, evidence_dir=evidence_dir, ctx=ctx)
        finally:
            _apply_harness_cleanup(phase0_cfg=phase0_cfg, cleanup=cleanup_cfg)
        try:
            materialize_action_evidence(
                adapter_manifest=adapter_manifest,
                repo_root=repo_root,
                run_dir=output,
                evidence_dir=evidence_dir,
                dry_run_ingest_events=dry_run_ingest_events,
            )
        except ActionEvidenceMaterializationError as e:
            raise AgentAdapterError(str(e)) from e
        AuditEngine().run(episode_dir=evidence_dir, case_specs=case_specs)
        _copy_episode_summary(evidence_dir=evidence_dir, episode_dir=episode_dir)
        episode_evidence_dirs.append((episode_dir, evidence_dir))
        episode_idx += 1

    finalize_result = finalize_run_manifest_action_evidence(run_dir=output)
    final_level = str(finalize_result.get("final_action_trace_level") or "none").strip()
    final_source = str(finalize_result.get("final_action_trace_source") or "none").strip()

    expected_level = str(finalize_result.get("expected_action_trace_level") or "").strip().upper()
    strict_failed = bool(
        strict_action_evidence and expected_level in {"L0", "L1", "L2"} and final_level == "none"
    )

    strict_failure_class = "infra_failed"
    strict_reason = (
        f"expected action_trace_level={expected_level} "
        "but no valid device_input_trace.jsonl produced"
    )

    for episode_dir, evidence_dir in episode_evidence_dirs:
        _patch_episode_summaries_action_evidence(
            episode_dir=episode_dir,
            action_trace_level=final_level,
            action_trace_source=final_source,
            strict_failure=strict_failed,
            strict_failure_class=strict_failure_class,
            strict_failure_reason=strict_reason,
        )
        _audit_evidence_dir(evidence_dir, action_trace_level=final_level)

    if strict_failed:
        raise AgentAdapterError(strict_reason)


def _ingest_trajectory(
    *,
    agent_id: str,
    registry_entry: Dict[str, Any],
    trajectory: Path,
    output: Path,
    seed: int,
    repo_root: Path,
    schemas_dir: Path,
    phase0_cfg: Phase0Config,
    run_metadata: Dict[str, Any],
) -> None:
    del schemas_dir

    episode_dir = output / "episode_0000"
    evidence_dir = _ensure_episode_layout(episode_dir=episode_dir)

    case_id = f"audit_only_ingest_{agent_id}"
    steps: list[dict[str, Any]] = []

    for raw in trajectory.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            continue
        steps.append(obj)
        if isinstance(obj.get("task_id"), str) and obj["task_id"].strip():
            case_id = obj["task_id"].strip()
        elif isinstance(obj.get("case_id"), str) and obj["case_id"].strip():
            case_id = obj["case_id"].strip()

    writer = EvidenceWriter(
        run_dir=output,
        case_id=case_id,
        seed=seed,
        run_mode="audit_only",
        metadata={
            **(run_metadata or {}),
            "agent_id": agent_id,
            "trajectory_path": str(trajectory),
            "trajectory_format": registry_entry.get("trajectory_format"),
        },
        episode_dir=evidence_dir,
    )

    for i, item in enumerate(steps):
        step = item.get("step")
        if not isinstance(step, int):
            step = item.get("step_idx")
        if not isinstance(step, int):
            step = i

        obs = item.get("observation")
        if not isinstance(obs, dict):
            obs = {}

        ui_text = str(obs.get("ui_text") or f"audit_only step {step}")
        action = item.get("action")
        if not isinstance(action, dict):
            action = {"type": "wait", "note": "audit_only_ingest"}

        observation_payload = {
            "foreground_package": obs.get("foreground_package") or "unknown",
            "foreground_activity": obs.get("foreground_activity") or None,
            "screen_info": obs.get("screen_info")
            or {"width_px": 1080, "height_px": 1920, "density_dpi": 440, "surface_orientation": 0},
            "a11y_tree": obs.get("a11y_tree")
            or {
                "nodes": [
                    {"id": "root", "role": "window", "children": ["label"]},
                    {
                        "id": "label",
                        "role": "text",
                        "text": ui_text,
                        "bounds": [0, 0, 200, 50],
                    },
                ]
            },
            "ui_hash": obs.get("ui_hash") or stable_sha256(ui_text.encode("utf-8")),
        }

        writer.record_observation(step=step, observation=observation_payload)
        writer.record_agent_call(
            {
                "step_idx": step,
                "agent_name": agent_id,
                "provider": None,
                "model_id": None,
                "base_url": None,
                "input_digest": stable_sha256({"step": step, "obs": observation_payload}),
                "response_digest": stable_sha256(action),
                "latency_ms": 0,
                "tokens_in": None,
                "tokens_out": None,
                "error": None,
            }
        )
        writer.record_agent_action(step=step, action=action)
        writer.record_action(step=step, action=action, result={"ok": True, "source": "trajectory"})

    if not steps:
        writer.record_device_event({"event": "trajectory_empty"})

    last_action = steps[-1].get("action") if steps else None
    finished = isinstance(last_action, dict) and last_action.get("type") in {"finished", "stop"}
    summary = {
        "status": "inconclusive",
        "steps": len(steps),
        "terminated_reason": "trajectory_end",
        "failure_class": None,
        "task_success": {
            "score": 0.0,
            "success": bool(finished),
            "conclusive": False,
            "reason": "audit_only_ingest",
            "oracle_id": None,
            "oracle_type": None,
        },
        "violations": [],
        "notes": {
            "runner": "audit_only_ingest",
            "trajectory_lines": len(steps),
        },
    }
    writer.write_summary(summary)
    writer.close()

    AuditEngine().run(episode_dir=evidence_dir, case_ctx={"case_id": case_id})
    _copy_episode_summary(evidence_dir=evidence_dir, episode_dir=episode_dir)
    finalize_result = finalize_run_manifest_action_evidence(run_dir=output)
    final_level = str(finalize_result.get("final_action_trace_level") or "none").strip()
    final_source = str(finalize_result.get("final_action_trace_source") or "none").strip()
    _patch_episode_summaries_action_evidence(
        episode_dir=episode_dir,
        action_trace_level=final_level,
        action_trace_source=final_source,
        strict_failure=False,
        strict_failure_class="infra_failed",
        strict_failure_reason="",
    )
    _audit_evidence_dir(evidence_dir, action_trace_level=final_level)
