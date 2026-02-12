from __future__ import annotations

import os
import re
import shlex
import time
from typing import Any, Dict, Optional

from mas_harness.runtime.android.controller import AndroidController


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return int(v)
        return int(v)
    except Exception:
        return None


def _adb_shell_cmd(parts: list[str]) -> str:
    return " ".join(shlex.quote(p) for p in parts)


_KEY_TO_KEYEVENT: dict[str, str] = {
    "enter": "66",  # KEYCODE_ENTER
    "backspace": "67",  # KEYCODE_DEL
    "delete": "67",  # KEYCODE_DEL
    "del": "67",  # KEYCODE_DEL
    "space": "62",  # KEYCODE_SPACE
    "tab": "61",  # KEYCODE_TAB
    "escape": "111",  # KEYCODE_ESCAPE
    "esc": "111",  # KEYCODE_ESCAPE
}


_OPEN_APP_ALIASES: dict[str, dict[str, str]] = {
    "settings": {"intent": "android.settings.SETTINGS", "expected_package": "com.android.settings"},
    "设置": {"intent": "android.settings.SETTINGS", "expected_package": "com.android.settings"},
    "wifi": {
        "intent": "android.settings.WIFI_SETTINGS",
        "expected_package": "com.android.settings",
    },
    "无线": {
        "intent": "android.settings.WIFI_SETTINGS",
        "expected_package": "com.android.settings",
    },
    "bluetooth": {
        "intent": "android.settings.BLUETOOTH_SETTINGS",
        "expected_package": "com.android.settings",
    },
    "蓝牙": {
        "intent": "android.settings.BLUETOOTH_SETTINGS",
        "expected_package": "com.android.settings",
    },
    "location": {
        "intent": "android.settings.LOCATION_SOURCE_SETTINGS",
        "expected_package": "com.android.settings",
    },
    "位置信息": {
        "intent": "android.settings.LOCATION_SOURCE_SETTINGS",
        "expected_package": "com.android.settings",
    },
}


class AndroidExecutor:
    def __init__(
        self,
        *,
        controller: AndroidController,
        timeout_s: float = 30.0,
        open_app_timeout_s: float = 5.0,
        device_input_writer: Any | None = None,
        device_input_source_level: str | None = None,
    ) -> None:
        self._controller = controller
        self._timeout_s = float(timeout_s)
        self._open_app_timeout_s = float(open_app_timeout_s)
        self._current_obs_digest: Optional[str] = None
        self._device_input_writer = device_input_writer
        self._device_input_source_level = (
            str(device_input_source_level).strip()
            if device_input_source_level is not None
            else None
        )
        if self._device_input_source_level is not None and self._device_input_source_level not in {
            "L0",
            "L1",
            "L2",
        }:
            raise ValueError("device_input_source_level must be one of: L0, L1, L2, null")

    def set_current_obs_digest(self, obs_digest: Optional[str]) -> None:
        self._current_obs_digest = obs_digest if isinstance(obs_digest, str) else None

    def _maybe_record_device_input(
        self,
        *,
        action: Dict[str, Any],
        event_type: str,
        payload: Dict[str, Any],
        mapping_warnings: list[str] | None = None,
    ) -> None:
        writer = self._device_input_writer
        level = self._device_input_source_level
        if writer is None or level is None:
            return

        record = getattr(writer, "record_device_input_event", None)
        if not callable(record):
            return

        step_idx = action.get("step_idx")
        if step_idx is None:
            return

        record(
            step_idx,
            step_idx if level == "L0" else action.get("ref_step_idx"),
            level,
            str(event_type),
            dict(payload) if isinstance(payload, dict) else {},
            None,
            list(mapping_warnings) if isinstance(mapping_warnings, list) else [],
        )

    def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        action_type = str(action.get("type") or "").strip().lower()
        ref_check_applicable = action.get("ref_check_applicable")
        if ref_check_applicable is None:
            ref_check_applicable = action.get("ref_obs_digest") is not None
        if ref_check_applicable:
            ref_obs_digest = action.get("ref_obs_digest")
            current_obs_digest = self._current_obs_digest
            if not isinstance(ref_obs_digest, str) or not ref_obs_digest.strip():
                self._maybe_record_device_input(
                    action=action,
                    event_type=action_type or "unknown",
                    payload={"executed": False, "error": "ref_obs_digest_missing"},
                )
                return {
                    "ok": False,
                    "error": "ref_obs_digest_missing",
                    "agent_failed": True,
                    "ref_obs_digest": ref_obs_digest,
                    "obs_digest": current_obs_digest,
                    "action_obs_digest": action.get("obs_digest"),
                }
            if not isinstance(current_obs_digest, str) or not current_obs_digest.strip():
                self._maybe_record_device_input(
                    action=action,
                    event_type=action_type or "unknown",
                    payload={"executed": False, "error": "current_obs_digest_missing"},
                )
                return {
                    "ok": False,
                    "error": "current_obs_digest_missing",
                    "infra_failed": True,
                    "ref_obs_digest": ref_obs_digest,
                    "obs_digest": current_obs_digest,
                    "action_obs_digest": action.get("obs_digest"),
                }
            if ref_obs_digest != current_obs_digest:
                self._maybe_record_device_input(
                    action=action,
                    event_type=action_type or "unknown",
                    payload={"executed": False, "error": "ref_obs_digest_mismatch"},
                )
                return {
                    "ok": False,
                    "error": "ref_obs_digest_mismatch",
                    "agent_failed": True,
                    "ref_obs_digest": ref_obs_digest,
                    "obs_digest": current_obs_digest,
                    "action_obs_digest": action.get("obs_digest"),
                }

        if action_type in {"tap", "long_press"}:
            coord_space = action.get("coord_space")
            if coord_space not in (None, "", "physical_px"):
                return {
                    "ok": False,
                    "error": "unsupported_coord_space",
                    "coord_space": coord_space,
                    "expected": "physical_px",
                }

            coord = action.get("coord")
            if not isinstance(coord, dict):
                coord = {}
            x = _safe_int(
                coord.get("x_px") if "x_px" in coord else action.get("x_px", action.get("x"))
            )
            y = _safe_int(
                coord.get("y_px") if "y_px" in coord else action.get("y_px", action.get("y"))
            )
            if x is None or y is None:
                return {"ok": False, "error": "missing_coord_px"}

            duration_ms = _safe_int(action.get("duration_ms"))
            if duration_ms is None:
                duration_ms = 800 if action_type == "long_press" else None

            if duration_ms is not None and duration_ms > 0:
                cmd = _adb_shell_cmd(
                    ["input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)]
                )
            else:
                cmd = _adb_shell_cmd(["input", "tap", str(x), str(y)])
            res = self._controller.adb_shell(cmd, timeout_s=self._timeout_s, check=False)
            self._maybe_record_device_input(
                action=action,
                event_type=action_type,
                payload={
                    "coord_space": "physical_px",
                    "x": int(x),
                    "y": int(y),
                    "duration_ms": duration_ms,
                },
            )
            return {
                "ok": bool(res.returncode == 0),
                "adb_returncode": getattr(res, "returncode", None),
                "stdout": getattr(res, "stdout", None),
                "stderr": getattr(res, "stderr", None),
            }

        if action_type == "swipe":
            coord_space = action.get("coord_space")
            if coord_space not in (None, "", "physical_px"):
                return {
                    "ok": False,
                    "error": "unsupported_coord_space",
                    "coord_space": coord_space,
                    "expected": "physical_px",
                }

            start = action.get("start")
            end = action.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                return {"ok": False, "error": "missing_swipe_points"}

            sx = _safe_int(start.get("x_px"))
            sy = _safe_int(start.get("y_px"))
            ex = _safe_int(end.get("x_px"))
            ey = _safe_int(end.get("y_px"))
            if None in (sx, sy, ex, ey):
                return {"ok": False, "error": "missing_swipe_coord_px"}

            duration_ms = _safe_int(action.get("duration_ms"))
            if duration_ms is None:
                duration_ms = 300
            cmd = _adb_shell_cmd(
                [
                    "input",
                    "swipe",
                    str(sx),
                    str(sy),
                    str(ex),
                    str(ey),
                    str(max(0, int(duration_ms))),
                ]
            )
            res = self._controller.adb_shell(cmd, timeout_s=self._timeout_s, check=False)
            self._maybe_record_device_input(
                action=action,
                event_type="swipe",
                payload={
                    "coord_space": "physical_px",
                    "start": {"x": int(sx), "y": int(sy)},
                    "end": {"x": int(ex), "y": int(ey)},
                    "duration_ms": duration_ms,
                },
            )
            return {
                "ok": bool(res.returncode == 0),
                "adb_returncode": getattr(res, "returncode", None),
                "stdout": getattr(res, "stdout", None),
                "stderr": getattr(res, "stderr", None),
            }

        if action_type == "type":
            res = self._execute_type(action)
            key_raw = action.get("key")
            key = str(key_raw).strip().lower() if key_raw is not None else ""
            text_raw = action.get("text")
            text = (
                text_raw
                if isinstance(text_raw, str)
                else ("" if text_raw is None else str(text_raw))
            )
            self._maybe_record_device_input(
                action=action,
                event_type="type",
                payload={"text": text, "key": key or None},
            )
            return res

        if action_type == "open_app":
            res = self._execute_open_app(action)
            pkg = action.get("package") or action.get("package_name") or action.get("app_package")
            pkg_str = str(pkg).strip() if pkg is not None else ""
            self._maybe_record_device_input(
                action=action,
                event_type="open_app",
                payload={"package": pkg_str or None},
            )
            return res

        if action_type == "open_url":
            res = self._execute_open_url(action)
            url = action.get("url")
            url_str = str(url).strip() if url is not None else ""
            self._maybe_record_device_input(
                action=action,
                event_type="open_url",
                payload={"url": url_str or None},
            )
            return res

        if action_type == "wait":
            duration_ms = _safe_int(action.get("duration_ms") or action.get("ms"))
            if duration_ms is None:
                duration_ms = 250
            time.sleep(max(0.0, float(duration_ms) / 1000.0))
            self._maybe_record_device_input(
                action=action,
                event_type="wait",
                payload={"duration_ms": int(duration_ms)},
            )
            return {"ok": True, "slept_ms": int(duration_ms)}

        if action_type == "press_back":
            cmd = _adb_shell_cmd(["input", "keyevent", "KEYCODE_BACK"])
            res = self._controller.adb_shell(cmd, timeout_s=self._timeout_s, check=False)
            self._maybe_record_device_input(
                action=action,
                event_type="press_back",
                payload={"keyevent": "KEYCODE_BACK"},
            )
            return {
                "ok": bool(res.returncode == 0),
                "adb_returncode": getattr(res, "returncode", None),
            }

        if action_type == "home":
            cmd = _adb_shell_cmd(["input", "keyevent", "KEYCODE_HOME"])
            res = self._controller.adb_shell(cmd, timeout_s=self._timeout_s, check=False)
            self._maybe_record_device_input(
                action=action,
                event_type="home",
                payload={"keyevent": "KEYCODE_HOME"},
            )
            return {
                "ok": bool(res.returncode == 0),
                "adb_returncode": getattr(res, "returncode", None),
            }

        if action_type in {"finished", "stop"}:
            self._maybe_record_device_input(action=action, event_type="finished", payload={})
            return {"ok": True, "note": "no-op"}

        self._maybe_record_device_input(
            action=action,
            event_type=action_type or "unknown",
            payload={
                "executed": False,
                "error": f"unsupported_action_type:{action_type or 'missing'}",
            },
        )
        return {"ok": False, "error": f"unsupported_action_type:{action_type or 'missing'}"}

    def _execute_type(self, action: Dict[str, Any]) -> Dict[str, Any]:
        key_raw = action.get("key")
        key = str(key_raw).strip().lower() if key_raw is not None else ""
        text_raw = action.get("text")
        text = (
            text_raw if isinstance(text_raw, str) else ("" if text_raw is None else str(text_raw))
        )

        adb_returncode = 0
        stdout: str | None = None
        stderr: str | None = None

        if text:
            for cmd in self._type_text_cmds(text):
                res = self._controller.adb_shell(cmd, timeout_s=self._timeout_s, check=False)
                adb_returncode = int(getattr(res, "returncode", 1) or 0)
                stdout = getattr(res, "stdout", stdout)
                stderr = getattr(res, "stderr", stderr)
                if adb_returncode != 0:
                    return {
                        "ok": False,
                        "error": "adb_input_text_failed",
                        "adb_returncode": adb_returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                    }

        if key:
            keycode = _KEY_TO_KEYEVENT.get(key)
            if keycode is None:
                return {"ok": False, "error": "unsupported_key", "key": key}
            cmd = _adb_shell_cmd(["input", "keyevent", keycode])
            res = self._controller.adb_shell(cmd, timeout_s=self._timeout_s, check=False)
            adb_returncode = int(getattr(res, "returncode", 1) or 0)
            stdout = getattr(res, "stdout", stdout)
            stderr = getattr(res, "stderr", stderr)
            if adb_returncode != 0:
                return {
                    "ok": False,
                    "error": "adb_keyevent_failed",
                    "key": key,
                    "adb_returncode": adb_returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                }

        return {"ok": True, "adb_returncode": adb_returncode, "stdout": stdout, "stderr": stderr}

    def _execute_open_url(self, action: Dict[str, Any]) -> Dict[str, Any]:
        url = action.get("url")
        url_str = str(url).strip() if url is not None else ""
        if not url_str:
            return {"ok": False, "error": "missing_url"}

        url_expanded = os.path.expandvars(url_str)
        pkg = action.get("package") or action.get("package_name") or action.get("app_package")
        pkg_str = str(pkg).strip() if pkg is not None else ""
        parts = ["am", "start", "-a", "android.intent.action.VIEW", "-d", url_expanded]
        if pkg_str:
            # Ensure the URL is handled by the intended app (avoid
            # "Complete action using..." choosers).
            parts += ["-p", pkg_str]
        cmd = _adb_shell_cmd(parts)
        res = self._controller.adb_shell(cmd, timeout_s=self._timeout_s, check=False)
        return {
            "ok": bool(res.returncode == 0),
            "adb_returncode": getattr(res, "returncode", None),
            "stdout": getattr(res, "stdout", None),
            "stderr": getattr(res, "stderr", None),
        }

    def _execute_open_app(self, action: Dict[str, Any]) -> Dict[str, Any]:
        pkg = action.get("package") or action.get("package_name") or action.get("app_package")
        pkg_str = str(pkg).strip() if pkg is not None else ""
        if not pkg_str:
            return {"ok": False, "error": "missing_package"}

        expected_pkg = pkg_str
        alias = _OPEN_APP_ALIASES.get(pkg_str.strip().lower())
        if alias is not None and isinstance(alias, dict) and isinstance(alias.get("intent"), str):
            cmd = _adb_shell_cmd(["am", "start", "-a", alias["intent"]])
            expected_pkg = (
                str(alias.get("expected_package") or expected_pkg).strip() or expected_pkg
            )
        elif pkg_str == "com.android.settings":
            cmd = _adb_shell_cmd(["am", "start", "-a", "android.settings.SETTINGS"])
        else:
            cmd = _adb_shell_cmd(
                [
                    "monkey",
                    "-p",
                    pkg_str,
                    "-c",
                    "android.intent.category.LAUNCHER",
                    "1",
                ]
            )

        res = self._controller.adb_shell(cmd, timeout_s=self._timeout_s, check=False)

        matched = False
        last_fg: Dict[str, Any] | None = None
        deadline = time.time() + self._open_app_timeout_s
        while time.time() < deadline:
            try:
                last_fg = self._controller.get_foreground(timeout_s=2.0)
            except Exception:
                last_fg = None
            if isinstance(last_fg, dict) and last_fg.get("package") == expected_pkg:
                matched = True
                break
            time.sleep(0.25)

        return {
            "ok": bool(res.returncode == 0 and matched),
            "adb_returncode": getattr(res, "returncode", None),
            "stdout": getattr(res, "stdout", None),
            "stderr": getattr(res, "stderr", None),
            "foreground_matched": matched,
            "foreground_after": last_fg,
        }

    def _type_text_cmds(self, text: str) -> list[str]:
        cmds: list[str] = []

        parts = re.split(r"\r?\n", text)
        for i, part in enumerate(parts):
            if part:
                safe = part.replace(" ", "%s")
                cmds.append(_adb_shell_cmd(["input", "text", safe]))
            if i < len(parts) - 1:
                cmds.append(_adb_shell_cmd(["input", "keyevent", "66"]))

        if not cmds:
            return []
        return cmds
