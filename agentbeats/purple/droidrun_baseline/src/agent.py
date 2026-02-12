from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Message, Part, TaskState, TextPart
from a2a.utils import get_message_text, new_agent_text_message
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

DROIDRUN_LOCK = asyncio.Lock()
PORTAL_PACKAGE_NAME = "com.droidrun.portal"
PORTAL_ACCESSIBILITY_SERVICE = (
    "com.droidrun.portal/com.droidrun.portal.service.DroidrunAccessibilityService"
)


class PortalUnhealthyError(RuntimeError):
    pass


async def _adb_shell(
    serial: str, *args: str, timeout_s: float = 15.0
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "adb",
        "-s",
        str(serial),
        "shell",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    return proc.returncode, stdout, stderr


async def _adb_cmd(serial: str, *args: str, timeout_s: float = 15.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "adb",
        "-s",
        str(serial),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    return proc.returncode, stdout, stderr


async def _portal_package_installed(serial: str) -> bool:
    # `pm path <package>` prints `package:/...` if installed; otherwise prints an error.
    rc, stdout, stderr = await _adb_shell(serial, "pm", "path", PORTAL_PACKAGE_NAME, timeout_s=15.0)
    out = (stdout or stderr).strip().strip("\r")
    return rc == 0 and out.startswith("package:")


def _resolve_portal_apk_url(*, debug: bool = False) -> tuple[str, str]:
    forced_url = str(os.getenv("DROIDRUN_PORTAL_APK_URL") or "").strip()
    if forced_url:
        return forced_url, "env:DROIDRUN_PORTAL_APK_URL"

    forced_version = str(os.getenv("DROIDRUN_PORTAL_VERSION") or "").strip()
    if forced_version:
        version = forced_version if forced_version.startswith("v") else f"v{forced_version}"
        download_base = str(
            os.getenv("DROIDRUN_PORTAL_DOWNLOAD_BASE")
            or "https://github.com/droidrun/droidrun-portal/releases/download"
        ).strip()
        asset_name = "droidrun-portal"
        return f"{download_base}/{version}/{asset_name}-{version}.apk", f"env:DROIDRUN_PORTAL_VERSION={version}"

    def _fetch_latest_asset_url() -> str:
        import json as _json
        from urllib.request import Request, urlopen

        hosts = ["https://api.github.com", "https://ungh.cc"]
        for host in hosts:
            try:
                url = f"{host}/repos/droidrun/droidrun-portal/releases/latest"
                req = Request(url, headers={"User-Agent": "agentbeats"})
                with urlopen(req, timeout=10) as resp:  # nosec - controlled URL + short timeout
                    body = resp.read().decode("utf-8", errors="replace")
                latest = _json.loads(body)
                assets = (
                    latest.get("release", {}).get("assets")
                    if isinstance(latest, dict) and "release" in latest
                    else latest.get("assets") if isinstance(latest, dict) else None
                )
                if not isinstance(assets, list):
                    continue
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    name = str(asset.get("name") or "")
                    url = str(asset.get("browser_download_url") or asset.get("downloadUrl") or "")
                    if name.startswith("droidrun-portal") and name.endswith(".apk") and url:
                        return url
            except Exception:
                continue
        raise RuntimeError("Failed to resolve latest DroidRun Portal APK asset URL")

    if str(os.getenv("DROIDRUN_PORTAL_LATEST") or "").strip().lower() in {"1", "true", "yes"}:
        return _fetch_latest_asset_url(), "env:DROIDRUN_PORTAL_LATEST"

    from droidrun import __version__ as droidrun_version
    from droidrun.portal import get_compatible_portal_version

    portal_version, download_base, mapping_fetched = get_compatible_portal_version(
        droidrun_version, debug
    )
    if portal_version:
        asset_name = "droidrun-portal"
        return (
            f"{download_base}/{portal_version}/{asset_name}-{portal_version}.apk",
            f"mapping:droidrun={droidrun_version}->portal={portal_version}",
        )

    if not mapping_fetched:
        logger.warning("Could not fetch portal version mapping; falling back to latest portal APK.")
    return _fetch_latest_asset_url(), "fallback:latest"


async def _install_droidrun_portal(serial: str) -> bool:
    logger.warning("DroidRun Portal not detected; attempting auto-install (serial=%s)...", serial)

    try:
        apk_url, apk_source = _resolve_portal_apk_url(debug=False)
        logger.info("Resolved Portal APK URL (%s): %s", apk_source, apk_url)
    except Exception as exc:
        logger.error("Failed to resolve Portal APK download URL: %s", exc)
        return False

    def _download(url: str, dest: Path) -> None:
        import shutil
        from urllib.request import Request, urlopen

        req = Request(url, headers={"User-Agent": "agentbeats"})
        with urlopen(req, timeout=30) as resp:  # nosec - controlled URL + timeout
            with dest.open("wb") as f:
                shutil.copyfileobj(resp, f)

    tmp_apk = Path(tempfile.gettempdir()) / f"agentbeats_{PORTAL_PACKAGE_NAME}_{uuid4().hex}.apk"
    download_timeout_s = float(os.getenv("DROIDRUN_PORTAL_DOWNLOAD_TIMEOUT_S") or "90")
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_download, apk_url, tmp_apk),
            timeout=download_timeout_s,
        )
    except asyncio.TimeoutError:
        logger.error("Timed out downloading Portal APK after %.1fs: %s", download_timeout_s, apk_url)
        with contextlib.suppress(FileNotFoundError):
            tmp_apk.unlink()
        return False
    except Exception as exc:
        logger.error("Failed to download Portal APK: %s", exc)
        with contextlib.suppress(FileNotFoundError):
            tmp_apk.unlink()
        return False

    try:
        # Best-effort cleanup (ignore failures; might not be installed yet).
        with contextlib.suppress(Exception):
            await _adb_cmd(serial, "uninstall", PORTAL_PACKAGE_NAME, timeout_s=60.0)

        rc, stdout, stderr = await _adb_cmd(
            serial,
            "install",
            "-r",
            "-g",
            str(tmp_apk),
            timeout_s=180.0,
        )
        combined = (stdout or "") + (stderr or "")
        if rc != 0 or "Failure" in combined:
            logger.error(
                "Portal install failed (rc=%s). stdout=%r stderr=%r",
                rc,
                stdout[-500:],
                stderr[-500:],
            )
            return False
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_apk.unlink()

    if not await _portal_package_installed(serial):
        logger.error("Portal install finished but package still not present: %s", PORTAL_PACKAGE_NAME)
        return False

    # Re-apply key device settings for droidrun.
    await _ensure_portal_accessibility(serial)
    with contextlib.suppress(Exception):
        await _adb_shell(serial, "ime", "enable", "com.droidrun.portal/.input.DroidrunKeyboardIME")
    with contextlib.suppress(Exception):
        await _adb_shell(serial, "ime", "set", "com.droidrun.portal/.input.DroidrunKeyboardIME")

    await _recover_active_window(serial)
    return True


def _parse_content_provider_result(raw_output: str) -> Any | None:
    lines = raw_output.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if "result=" in line:
            json_str = line.split("result=", 1)[1].strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue

        if line.startswith("{") or line.startswith("["):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    try:
        return json.loads(raw_output.strip())
    except json.JSONDecodeError:
        return None


def _unwrap_portal_envelope(data: Any) -> Any:
    if not isinstance(data, dict):
        return data

    inner_key = "result" if "result" in data else "data" if "data" in data else None
    if not inner_key:
        return data

    inner_value = data.get(inner_key)
    if isinstance(inner_value, str):
        try:
            return json.loads(inner_value)
        except json.JSONDecodeError:
            return inner_value
    return inner_value


async def _portal_content_query(serial: str, uri: str) -> dict[str, Any]:
    rc, stdout, stderr = await _adb_shell(
        serial, "content", "query", "--uri", uri, timeout_s=15.0
    )
    raw = (stdout or stderr).strip()
    outer = _parse_content_provider_result(raw) if raw else None
    inner = _unwrap_portal_envelope(outer) if outer is not None else None
    return {
        "rc": rc,
        "uri": uri,
        "raw": raw,
        "outer": outer,
        "inner": inner,
    }


async def _ensure_portal_accessibility(serial: str) -> None:
    rc, stdout, stderr = await _adb_shell(
        serial, "settings", "get", "secure", "enabled_accessibility_services"
    )
    raw = (stdout or stderr).strip()
    existing = raw if rc == 0 else ""
    if not existing or existing == "null":
        merged = PORTAL_ACCESSIBILITY_SERVICE
    else:
        parts = [p for p in existing.split(":") if p]
        if PORTAL_ACCESSIBILITY_SERVICE not in parts:
            parts.append(PORTAL_ACCESSIBILITY_SERVICE)
        merged = ":".join(parts)

    await _adb_shell(
        serial, "settings", "put", "secure", "enabled_accessibility_services", merged
    )
    await _adb_shell(serial, "settings", "put", "secure", "accessibility_enabled", "1")


async def _recover_active_window(serial: str) -> None:
    # Best-effort attempts to ensure the device is interactive (screen on / unlocked),
    # without changing the currently focused app.
    cmds: list[tuple[str, ...]] = [
        ("input", "keyevent", "224"),  # KEYCODE_WAKEUP
        ("wm", "dismiss-keyguard"),
    ]
    for cmd in cmds:
        with contextlib.suppress(Exception):
            await _adb_shell(serial, *cmd, timeout_s=15.0)
    await asyncio.sleep(0.5)


def _compact_portal_diag(diag: dict[str, Any]) -> dict[str, Any]:
    ping = diag.get("ping") or {}
    state_full = diag.get("state_full") or {}

    ping_outer = ping.get("outer") if isinstance(ping, dict) else None
    state_outer = state_full.get("outer") if isinstance(state_full, dict) else None
    state_inner = state_full.get("inner") if isinstance(state_full, dict) else None

    compact: dict[str, Any] = {
        "ping": {
            "rc": ping.get("rc"),
            "uri": ping.get("uri"),
            "outer": ping_outer,
        }
    }

    if isinstance(state_outer, dict) and state_outer.get("status") == "success" and isinstance(
        state_inner, dict
    ):
        device_context = state_inner.get("device_context")
        screen_bounds = (
            device_context.get("screen_bounds")
            if isinstance(device_context, dict)
            else None
        )
        compact["state_full"] = {
            "rc": state_full.get("rc"),
            "uri": state_full.get("uri"),
            "outer_status": state_outer.get("status"),
            "phone_state": state_inner.get("phone_state"),
            "screen_bounds": screen_bounds,
        }
        return compact

    # Error / parse case: include raw output (small) + outer envelope.
    compact["state_full"] = {
        "rc": state_full.get("rc"),
        "uri": state_full.get("uri"),
        "raw": state_full.get("raw"),
        "outer": state_outer,
    }
    return compact


async def _ensure_portal_state_full_ok(
    serial: str, *, timeout_s: float = 15.0
) -> dict[str, Any]:
    start = time.monotonic()
    last: dict[str, Any] = {}
    did_force_stop = False

    while time.monotonic() - start < timeout_s:
        ping = await _portal_content_query(serial, "content://com.droidrun.portal/ping")
        state_full = await _portal_content_query(serial, "content://com.droidrun.portal/state_full")
        last = {"ping": ping, "state_full": state_full}

        outer = state_full.get("outer")
        if isinstance(outer, dict) and outer.get("status") == "success":
            inner = state_full.get("inner")
            if isinstance(inner, dict) and all(
                k in inner for k in ("a11y_tree", "phone_state", "device_context")
            ):
                return {"ok": True, **_compact_portal_diag(last)}

        err_str = None
        if isinstance(outer, dict):
            err_str = str(outer.get("message") or outer.get("error") or "")

        # If we keep failing (especially with "No active window"), a force-stop tends to
        # rebind the accessibility service cleanly.
        if not did_force_stop and time.monotonic() - start > min(5.0, timeout_s / 2):
            did_force_stop = True
            logger.warning("Portal still unhealthy; force-stopping portal app for reset...")
            with contextlib.suppress(Exception):
                await _adb_shell(serial, "am", "force-stop", "com.droidrun.portal", timeout_s=15.0)
            await _ensure_portal_accessibility(serial)
            await _recover_active_window(serial)
            await asyncio.sleep(0.5)
            continue

        if "Accessibility service not available" in (err_str or ""):
            logger.warning("Portal accessibility service unavailable; attempting recovery...")
            await _ensure_portal_accessibility(serial)
            await _recover_active_window(serial)
        elif "No active window" in (err_str or "") or "root filtered out" in (err_str or ""):
            logger.warning("Portal state_full has no active window; attempting recovery...")
            await _ensure_portal_accessibility(serial)
            await _recover_active_window(serial)
        else:
            # Common flake: state_full returns `No active window or root filtered out` even
            # when the emulator is booted; re-applying the accessibility setting often
            # re-binds the service.
            await _ensure_portal_accessibility(serial)
            await _recover_active_window(serial)

        await asyncio.sleep(0.5)

    return {"ok": False, **_compact_portal_diag(last)}


class Timeouts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_s: float = Field(..., ge=0)
    max_steps: int | None = Field(default=None, ge=0)


class TaskRequest(BaseModel):
    """Minimal Step 3 contract: green -> purple."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    variant: Any | None = None
    goal: str
    adb_server: str
    android_serial: str
    timeouts: Timeouts


class TaskResult(BaseModel):
    """Minimal Step 3 contract: purple -> green."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "fail", "timeout", "error"]
    summary: str
    artifacts: dict[str, Any] | None = None


def _parse_adb_server(raw: str) -> tuple[str, int] | None:
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("tcp:"):
        s = s.removeprefix("tcp:").strip()
    if not s:
        return None

    if s.startswith("["):
        # Best-effort IPv6: [::1]:5037
        if "]" not in s:
            return None
        host, rest = s[1:].split("]", 1)
        host = host.strip()
        rest = rest.strip()
        if not host:
            return None
        if rest.startswith(":"):
            rest = rest[1:]
        port_str = rest.strip() or "5037"
    else:
        if ":" in s:
            host, port_str = s.rsplit(":", 1)
            host = host.strip()
            port_str = port_str.strip()
        else:
            host, port_str = s.strip(), "5037"

    if not host:
        return None
    try:
        port = int(port_str)
    except Exception:
        return None
    if port <= 0 or port > 65535:
        return None
    return host, port


@contextlib.contextmanager
def _temporary_environ(overrides: dict[str, str | None]):
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


def _list_dirs(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return [p for p in path.iterdir() if p.is_dir()]


def _pick_newest_dir(dirs: set[Path]) -> Path | None:
    candidates = [d for d in dirs if d.exists() and d.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _detect_trace_dir(*, base_dir: Path, before: set[Path]) -> Path | None:
    after = set(_list_dirs(base_dir))
    created = after - set(before)
    if len(created) == 1:
        return next(iter(created))
    if created:
        return _pick_newest_dir(created)
    return _pick_newest_dir(after)


def _result_field(result: Any, key: str) -> Any:
    if isinstance(result, dict):
        return result.get(key)
    return getattr(result, key, None)


def _resolve_api_key() -> str | None:
    return os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")


def _resolve_trace_root() -> Path:
    raw = os.getenv("AGENTBEATS_PURPLE_TRACE_DIR")
    if raw and raw.strip():
        return Path(raw.strip()).expanduser()
    return Path(tempfile.gettempdir()) / "agentbeats_purple_droidrun"


async def _run_droidrun_goal(
    *,
    goal: str,
    max_steps: int,
    android_serial: str,
    trace_base_dir: Path,
    api_key: str,
    timeout_s: float | None = None,
) -> Any:
    from droidrun import (
        AgentConfig,
        DeviceConfig,
        DroidAgent,
        DroidrunConfig,
        ExecutorConfig,
        LoggingConfig,
        ManagerConfig,
    )
    from llama_index.llms.openai import OpenAI
    from llama_index.llms.openai_like import OpenAILike

    openrouter_base_url = os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
    manager_model = os.getenv("DROIDRUN_MANAGER_MODEL") or "gpt-5.1"
    executor_model = os.getenv("DROIDRUN_EXECUTOR_MODEL") or "google/gemini-3-flash-preview"
    codeact_model = os.getenv("DROIDRUN_CODEACT_MODEL") or "gpt-5-mini"

    config = DroidrunConfig(
        agent=AgentConfig(
            max_steps=int(max_steps),
            reasoning=True,
            after_sleep_action=1.0,
            wait_for_stable_ui=0.3,
            manager=ManagerConfig(vision=True),
            executor=ExecutorConfig(vision=True),
        ),
        logging=LoggingConfig(
            debug=True,
            save_trajectory="action",
            trajectory_path=str(trace_base_dir),
            trajectory_gifs=False,
            rich_text=False,
        ),
        device=DeviceConfig(
            serial=str(android_serial),
            platform="android",
            use_tcp=False,
        ),
    )

    droidrun_timeout = int(timeout_s) if timeout_s is not None else 1000
    agent = DroidAgent(
        goal=str(goal),
        config=config,
        llms={
            "manager": OpenAI(model=manager_model, api_base=openrouter_base_url, api_key=api_key),
            "executor": OpenAILike(
                model=executor_model,
                api_base=openrouter_base_url,
                api_key=api_key,
                is_chat_model=True,
            ),
            "codeact": OpenAI(model=codeact_model, api_base=openrouter_base_url, api_key=api_key),
            "text_manipulator": OpenAI(
                model=codeact_model,
                api_base=openrouter_base_url,
                api_key=api_key,
            ),
            "app_opener": OpenAI(
                model=codeact_model,
                api_base=openrouter_base_url,
                api_key=api_key,
            ),
            "scripter": OpenAI(
                model=codeact_model,
                api_base=openrouter_base_url,
                api_key=api_key,
            ),
            "structured_output": OpenAI(
                model=codeact_model,
                api_base=openrouter_base_url,
                api_key=api_key,
            ),
        },
        timeout=droidrun_timeout,
    )
    return await agent.run()


class Agent:
    async def run(self, message: Message, updater: TaskUpdater) -> None:
        input_text = get_message_text(message)

        try:
            request = TaskRequest.model_validate_json(input_text)
        except ValidationError as exc:
            await updater.reject(new_agent_text_message(f"Invalid request: {exc}"))
            return
        except json.JSONDecodeError as exc:
            await updater.reject(new_agent_text_message(f"Invalid JSON: {exc}"))
            return

        logger.info(
            "task_request received: %s",
            request.model_dump_json(indent=None, exclude_none=True),
        )

        await updater.update_status(
            TaskState.working,
            new_agent_text_message("Step 5: executing mobile task via droidrun."),
        )

        if isinstance(request.variant, dict) and request.variant.get("mode") == "dummy":
            result = TaskResult(
                status="success",
                summary="Dummy execution requested (variant.mode=dummy).",
                artifacts={"echo_request": request.model_dump(mode="json", exclude_none=True)},
            )
            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=result.summary)),
                    Part(root=DataPart(data=result.model_dump(mode="json", exclude_none=True))),
                ],
                name="task_result",
            )
            return

        delay_s: float | None = None
        if isinstance(request.variant, dict):
            raw = request.variant.get("simulate_delay_s") or request.variant.get("sleep_s")
            if raw is not None:
                try:
                    delay_s = float(raw)
                except Exception:
                    delay_s = None

        if delay_s and delay_s > 0:
            await asyncio.sleep(delay_s)
            result = TaskResult(
                status="success",
                summary=f"Dummy delay executed (sleep_s={delay_s:.2f}).",
                artifacts={"echo_request": request.model_dump(mode="json", exclude_none=True)},
            )
            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=result.summary)),
                    Part(root=DataPart(data=result.model_dump(mode="json", exclude_none=True))),
                ],
                name="task_result",
            )
            return

        adb = _parse_adb_server(request.adb_server)
        if adb is None:
            result = TaskResult(status="error", summary="Invalid adb_server.", artifacts=None)
            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=result.summary)),
                    Part(root=DataPart(data=result.model_dump(mode="json", exclude_none=True))),
                ],
                name="task_result",
            )
            return

        host, port = adb
        overrides = {
            "ADB_SERVER_SOCKET": f"tcp:{host}:{port}",
            # async_adbutils (used by droidrun) reads these env vars for the adb server address.
            "ANDROID_ADB_SERVER_HOST": host,
            "ANDROID_ADB_SERVER_PORT": str(port),
            "ANDROID_SERIAL": request.android_serial,
        }

        preflight_mode: str | None = None
        if isinstance(request.variant, dict):
            mode = str(request.variant.get("mode") or "").strip().lower()
            if mode in {"portal_preflight", "infra_preflight"}:
                preflight_mode = mode

        if preflight_mode is not None:
            portal_health: dict[str, Any] | None = None
            try:
                async with DROIDRUN_LOCK:
                    with _temporary_environ(overrides):
                        if not await _portal_package_installed(request.android_serial):
                            await _install_droidrun_portal(request.android_serial)

                        # Re-apply key device settings even if already installed.
                        await _ensure_portal_accessibility(request.android_serial)
                        with contextlib.suppress(Exception):
                            await _adb_shell(
                                request.android_serial,
                                "ime",
                                "enable",
                                "com.droidrun.portal/.input.DroidrunKeyboardIME",
                            )
                        with contextlib.suppress(Exception):
                            await _adb_shell(
                                request.android_serial,
                                "ime",
                                "set",
                                "com.droidrun.portal/.input.DroidrunKeyboardIME",
                            )
                        await _recover_active_window(request.android_serial)

                        portal_health = await _ensure_portal_state_full_ok(
                            request.android_serial,
                            timeout_s=min(max(30.0, float(request.timeouts.total_s)), 120.0),
                        )

                result = TaskResult(
                    status="success" if (portal_health or {}).get("ok") else "error",
                    summary=f"Portal preflight completed (mode={preflight_mode}).",
                    artifacts={
                        "adb_server_socket": overrides["ADB_SERVER_SOCKET"],
                        "android_serial": request.android_serial,
                        "portal_health": portal_health,
                    },
                )
            except Exception as exc:
                result = TaskResult(
                    status="error",
                    summary=f"Portal preflight error: {exc}",
                    artifacts={
                        "adb_server_socket": overrides["ADB_SERVER_SOCKET"],
                        "android_serial": request.android_serial,
                        "portal_health": portal_health,
                    },
                )

            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=result.summary)),
                    Part(root=DataPart(data=result.model_dump(mode="json", exclude_none=True))),
                ],
                name="task_result",
            )
            return

        api_key = _resolve_api_key()
        if not api_key:
            result = TaskResult(
                status="error",
                summary=(
                    "Missing API key. Set `OPENROUTER_API_KEY` (recommended) or `OPENAI_API_KEY`."
                ),
                artifacts=None,
            )
            await updater.add_artifact(
                parts=[
                    Part(root=TextPart(text=result.summary)),
                    Part(root=DataPart(data=result.model_dump(mode="json", exclude_none=True))),
                ],
                name="task_result",
            )
            return

        max_steps = request.timeouts.max_steps if request.timeouts.max_steps is not None else 50
        trace_root = _resolve_trace_root()
        trace_root.mkdir(parents=True, exist_ok=True)
        task_dir = trace_root / f"{int(time.time())}_{request.case_id}_{uuid4().hex[:8]}"
        task_dir.mkdir(parents=True, exist_ok=True)
        before_dirs = set(_list_dirs(task_dir))

        await updater.update_status(
            TaskState.working,
            new_agent_text_message(
                f"Running droidrun (goal={request.goal!r}, adb_server={host}:{port}, "
                f"serial={request.android_serial}, max_steps={max_steps})."
            ),
        )

        droidrun_result: Any = None
        droidrun_trace_dir: Path | None = None
        macro: dict[str, Any] | None = None
        portal_health: dict[str, Any] | None = None
        try:
            async with DROIDRUN_LOCK:
                with _temporary_environ(overrides):
                    preflight_started = time.monotonic()
                    if not await _portal_package_installed(request.android_serial):
                        await _install_droidrun_portal(request.android_serial)
                    portal_health = await _ensure_portal_state_full_ok(
                        request.android_serial,
                        timeout_s=min(
                            max(30.0, float(request.timeouts.total_s) * 0.5),
                            max(1.0, float(request.timeouts.total_s)),
                            120.0,
                        ),
                    )
                    preflight_elapsed = time.monotonic() - preflight_started
                    remaining_s = float(request.timeouts.total_s) - preflight_elapsed
                    if remaining_s <= 0:
                        raise asyncio.TimeoutError()
                    if not portal_health.get("ok"):
                        raise PortalUnhealthyError(
                            "DroidRun Portal state_full is unhealthy; see "
                            "task_result.artifacts.portal_health."
                        )

                    droidrun_result = await asyncio.wait_for(
                        _run_droidrun_goal(
                            goal=request.goal,
                            max_steps=int(max_steps),
                            android_serial=request.android_serial,
                            trace_base_dir=task_dir,
                            api_key=api_key,
                            timeout_s=max(1.0, remaining_s),
                        ),
                        timeout=remaining_s,
                    )

            droidrun_trace_dir = _detect_trace_dir(base_dir=task_dir, before=before_dirs)
            if droidrun_trace_dir is None and (task_dir / "macro.json").exists():
                droidrun_trace_dir = task_dir
            if droidrun_trace_dir is not None:
                macro_path = droidrun_trace_dir / "macro.json"
                if macro_path.exists():
                    try:
                        macro = json.loads(macro_path.read_text(encoding="utf-8"))
                    except Exception:
                        macro = None

            success = bool(_result_field(droidrun_result, "success") is True)
            steps = _result_field(droidrun_result, "steps")
            summary = f"droidrun {'succeeded' if success else 'finished'} (steps={steps!r})."
            status: Literal["success", "fail"] = "success" if success else "fail"
            result = TaskResult(
                status=status,
                summary=summary,
                artifacts={
                    "adb_server_socket": overrides["ADB_SERVER_SOCKET"],
                    "android_serial": request.android_serial,
                    "portal_health": portal_health,
                    "droidrun": {
                        "success": _result_field(droidrun_result, "success"),
                        "steps": steps,
                        "trace_dir": str(droidrun_trace_dir) if droidrun_trace_dir else None,
                        "macro": macro,
                    },
                },
            )
        except PortalUnhealthyError as exc:
            droidrun_trace_dir = _detect_trace_dir(base_dir=task_dir, before=before_dirs)
            if droidrun_trace_dir is None and (task_dir / "macro.json").exists():
                droidrun_trace_dir = task_dir
            if portal_health:
                logger.error("portal_health: %s", json.dumps(portal_health, ensure_ascii=False))
            result = TaskResult(
                status="error",
                summary=str(exc),
                artifacts={
                    "adb_server_socket": overrides["ADB_SERVER_SOCKET"],
                    "android_serial": request.android_serial,
                    "portal_health": portal_health,
                    "droidrun": {
                        "trace_dir": str(droidrun_trace_dir) if droidrun_trace_dir else None,
                    },
                },
            )
        except asyncio.TimeoutError:
            droidrun_trace_dir = _detect_trace_dir(base_dir=task_dir, before=before_dirs)
            if droidrun_trace_dir is None and (task_dir / "macro.json").exists():
                droidrun_trace_dir = task_dir
            result = TaskResult(
                status="timeout",
                summary=f"droidrun timed out after {request.timeouts.total_s:.2f}s.",
                artifacts={
                    "adb_server_socket": overrides["ADB_SERVER_SOCKET"],
                    "android_serial": request.android_serial,
                    "portal_health": portal_health,
                    "droidrun": {
                        "trace_dir": str(droidrun_trace_dir) if droidrun_trace_dir else None,
                    },
                },
            )
        except Exception as exc:
            logger.exception("droidrun execution failed")
            droidrun_trace_dir = _detect_trace_dir(base_dir=task_dir, before=before_dirs)
            if droidrun_trace_dir is None and (task_dir / "macro.json").exists():
                droidrun_trace_dir = task_dir
            if portal_health and not portal_health.get("ok"):
                logger.error("portal_health: %s", json.dumps(portal_health, ensure_ascii=False))
            result = TaskResult(
                status="error",
                summary=f"droidrun error: {exc}",
                artifacts={
                    "adb_server_socket": overrides["ADB_SERVER_SOCKET"],
                    "android_serial": request.android_serial,
                    "portal_health": portal_health,
                    "droidrun": {
                        "trace_dir": str(droidrun_trace_dir) if droidrun_trace_dir else None,
                    },
                },
            )

        await updater.add_artifact(
            parts=[
                Part(root=TextPart(text=result.summary)),
                Part(root=DataPart(data=result.model_dump(mode="json", exclude_none=True))),
            ],
            name="task_result",
        )
