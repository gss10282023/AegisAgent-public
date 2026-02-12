from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from mas_harness.evidence import _TINY_PNG_1X1
from mas_harness.runtime.android.controller import AndroidController
from mas_harness.runtime.android.executor import AndroidExecutor


@dataclass(frozen=True)
class AndroidObservation:
    screenshot_png: bytes
    screen_info: Dict[str, Any]
    foreground: Dict[str, Any]
    physical_frame_boundary_px: Optional[Dict[str, int]] = None
    uiautomator_xml: Optional[str] = None
    a11y_tree: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        fg_pkg = self.foreground.get("package") if isinstance(self.foreground, dict) else None
        fg_act = self.foreground.get("activity") if isinstance(self.foreground, dict) else None
        out: Dict[str, Any] = {
            "screenshot_png": self.screenshot_png,
            "screen_info": self.screen_info,
            "foreground": self.foreground,
            "foreground_package": fg_pkg,
            "foreground_activity": fg_act,
            "notifications": [],
            "clipboard": None,
        }
        if self.physical_frame_boundary_px is not None:
            out["physical_frame_boundary_px"] = self.physical_frame_boundary_px
        if self.uiautomator_xml is not None:
            out["uiautomator_xml"] = self.uiautomator_xml
        if self.a11y_tree is not None:
            out["a11y_tree"] = self.a11y_tree
        return out


class AndroidEnvAdapter:
    """Minimal adb-backed env used by agentctl fixed.

    This is intentionally small: enough to observe the screen and execute a
    small action subset so hard oracles (e.g. resumed_activity) can run.
    """

    def __init__(
        self,
        *,
        adb_path: str = "adb",
        serial: str,
        timeout_s: float = 30.0,
        uiautomator_timeout_s: float = 20.0,
        open_app_timeout_s: float = 5.0,
        evidence_writer: Any | None = None,
        action_trace_level: str | None = None,
    ) -> None:
        self._controller = AndroidController(
            adb_path=str(adb_path),
            serial=str(serial),
            timeout_s=float(timeout_s),
        )
        self._timeout_s = float(timeout_s)
        self._uiautomator_timeout_s = float(uiautomator_timeout_s)
        self._open_app_timeout_s = float(open_app_timeout_s)

        action_trace_level_norm = (
            str(action_trace_level).strip().upper() if action_trace_level else None
        )
        device_input_writer = evidence_writer if action_trace_level_norm == "L0" else None
        self._executor = AndroidExecutor(
            controller=self._controller,
            timeout_s=self._timeout_s,
            open_app_timeout_s=self._open_app_timeout_s,
            device_input_writer=device_input_writer,
            device_input_source_level="L0" if device_input_writer is not None else None,
        )

    @property
    def serial(self) -> Optional[str]:
        return self._controller.serial

    def __getattr__(self, name: str) -> Any:
        return getattr(self._controller, name)

    def observe(self, *, step: int | None = None, dump_ui: bool | None = None) -> Dict[str, Any]:
        if dump_ui is None:
            dump_ui = step is None or int(step) == 0

        try:
            screenshot_png = self._controller.screencap(timeout_s=self._timeout_s)
        except Exception:
            screenshot_png = _TINY_PNG_1X1

        try:
            screen_info = self._controller.get_screen_info(timeout_s=self._timeout_s)
        except Exception:
            screen_info = {"wm_size": None, "wm_density": None, "surface_orientation": None}

        physical_frame_boundary_px: Optional[Dict[str, int]] = None
        if isinstance(screen_info, dict):
            boundary = screen_info.get("physical_frame_boundary_px")
            if isinstance(boundary, dict):
                physical_frame_boundary_px = boundary  # best-effort passthrough

        try:
            foreground = self._controller.get_foreground(timeout_s=self._timeout_s)
        except Exception:
            foreground = {"package": None, "activity": None, "component": None}

        uiautomator_xml: Optional[str] = None
        a11y_tree: Optional[Dict[str, Any]] = None

        if dump_ui:
            try:
                with tempfile.TemporaryDirectory() as td:
                    xml_path = Path(td) / "uiautomator.xml"
                    self._controller.uiautomator_dump(
                        local_path=xml_path,
                        timeout_s=self._uiautomator_timeout_s,
                    )
                    uiautomator_xml = xml_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                # Fallback: provide a minimal a11y tree so EvidenceWriter can synthesize a
                # non-empty ui_elements list (required by the evidence auditor).
                a11y_tree = {
                    "nodes": [
                        {"id": "root", "role": "window", "children": ["label"]},
                        {
                            "id": "label",
                            "role": "text",
                            "text": "uiautomator_dump_failed",
                            "bounds": [0, 0, 400, 80],
                        },
                    ]
                }

        return AndroidObservation(
            screenshot_png=screenshot_png,
            screen_info=screen_info,
            foreground=foreground,
            physical_frame_boundary_px=physical_frame_boundary_px,
            uiautomator_xml=uiautomator_xml,
            a11y_tree=a11y_tree,
        ).to_dict()

    def reset(self, initial_state: Any | None = None) -> None:
        """Best-effort reset hook used by reset_grounding."""

        if not initial_state:
            return

        if isinstance(initial_state, Mapping):
            if "type" in initial_state:
                try:
                    self._executor.execute(dict(initial_state))
                except Exception:
                    pass
                return

            package = (
                initial_state.get("package")
                or initial_state.get("package_name")
                or initial_state.get("app_package")
                or initial_state.get("app")
            )
            url = initial_state.get("url") or initial_state.get("uri")

            # Fresh emulators/CI can show blocking system dialogs (e.g. "System UI isn't
            # responding") during early app startups. Best-effort: dismiss them before/after we
            # set initial state.
            try:
                self._maybe_dismiss_system_dialogs()
            except Exception:
                pass

            if package:
                try:
                    self._executor.execute({"type": "open_app", "package": package})
                except Exception:
                    pass

            # Fresh emulators/CI often show Chrome first-run/sign-in onboarding, which can block
            # `open_url` from landing on the intended page. Best-effort: detect onboarding UI,
            # click through safe "skip/continue" buttons, then re-open the URL once.
            try:
                if package == "com.android.chrome":
                    self._maybe_skip_chrome_first_run()
            except Exception:
                pass

            if url:
                try:
                    action: Dict[str, Any] = {"type": "open_url", "url": url}
                    if package:
                        action["package"] = package
                    self._executor.execute(action)
                except Exception:
                    pass

            try:
                self._maybe_dismiss_system_dialogs()
            except Exception:
                pass

            try:
                if package == "com.android.chrome" and self._maybe_skip_chrome_first_run():
                    if url:
                        self._executor.execute({"type": "open_url", "url": url, "package": package})
            except Exception:
                pass

    def _maybe_dismiss_system_dialogs(self) -> bool:
        raw = str(os.getenv("MAS_DISMISS_SYSTEM_DIALOGS", "1") or "").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False

        timeout_s_raw = os.getenv("MAS_DISMISS_SYSTEM_DIALOGS_TIMEOUT_S", "30")
        max_actions_raw = os.getenv("MAS_DISMISS_SYSTEM_DIALOGS_MAX_ACTIONS", "6")
        try:
            timeout_s = float(timeout_s_raw)
        except Exception:
            timeout_s = 30.0
        try:
            max_actions = int(max_actions_raw)
        except Exception:
            max_actions = 6

        deadline = time.time() + max(1.0, timeout_s)
        actions = 0
        tapped_any = False

        while time.time() < deadline and actions < max_actions:
            xml = self._try_dump_uiautomator_xml(timeout_s=10.0)
            if not isinstance(xml, str) or not xml.strip():
                time.sleep(0.5)
                continue

            # Only act when we see known "not responding" dialogs, to avoid tapping arbitrary
            # buttons.
            xml_cf = xml.casefold()
            if (
                "isn't responding" not in xml_cf
                and "is not responding" not in xml_cf
                and "not responding" not in xml_cf
                and "无响应" not in xml
            ):
                break

            candidate = self._pick_system_dialog_button(xml)
            if candidate is None:
                break

            x_px, y_px, label = candidate
            try:
                self._executor.execute(
                    {
                        "type": "tap",
                        "coord": {"x_px": int(x_px), "y_px": int(y_px)},
                    }
                )
            except Exception:
                time.sleep(0.5)
                continue

            tapped_any = True
            actions += 1
            time.sleep(0.8)

        return tapped_any

    def _pick_system_dialog_button(self, xml: str) -> Optional[tuple[int, int, str]]:
        # Prefer "Wait" (dismiss dialog without killing apps), then "OK".
        allow_patterns: list[tuple[str, int]] = [
            ("wait", 100),
            ("close app", 90),
            ("ok", 80),
            ("确定", 80),
            ("等待", 100),
            ("关闭应用", 90),
        ]

        def parse_bounds(bounds: str) -> Optional[tuple[int, int, int, int]]:
            m = re.match(r"^\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]$", str(bounds).strip())
            if not m:
                return None
            try:
                x1, y1, x2, y2 = (
                    int(m.group(1)),
                    int(m.group(2)),
                    int(m.group(3)),
                    int(m.group(4)),
                )
            except Exception:
                return None
            if x2 <= x1 or y2 <= y1:
                return None
            return (x1, y1, x2, y2)

        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml)
        except Exception:
            return None

        candidates: list[tuple[int, int, int, str]] = []
        for el in root.iter():
            if el.tag != "node":
                continue
            if str(el.attrib.get("clickable") or "") != "true":
                continue
            if str(el.attrib.get("enabled") or "") == "false":
                continue

            text = str(el.attrib.get("text") or "").strip()
            desc = str(el.attrib.get("content-desc") or "").strip()
            label = text or desc
            if not label:
                continue

            label_cf = label.casefold()
            score = 0
            for pat, val in allow_patterns:
                if pat in label_cf:
                    score = max(score, int(val))
            if score <= 0:
                continue

            rect = parse_bounds(str(el.attrib.get("bounds") or ""))
            if rect is None:
                continue
            x1, y1, x2, y2 = rect
            x = int((x1 + x2) // 2)
            y = int((y1 + y2) // 2)
            candidates.append((score, y, x, label))

        if not candidates:
            return None

        candidates.sort(key=lambda t: (-t[0], -t[1], t[2], t[3]))
        _score, _y, _x, label = candidates[0]
        return (_x, _y, label)

    def _maybe_skip_chrome_first_run(self) -> bool:
        raw = str(os.getenv("MAS_SKIP_CHROME_FIRST_RUN", "1") or "").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False

        timeout_s_raw = os.getenv("MAS_SKIP_CHROME_FIRST_RUN_TIMEOUT_S", "120")
        max_actions_raw = os.getenv("MAS_SKIP_CHROME_FIRST_RUN_MAX_ACTIONS", "12")
        try:
            timeout_s = float(timeout_s_raw)
        except Exception:
            timeout_s = 60.0
        try:
            max_actions = int(max_actions_raw)
        except Exception:
            max_actions = 12

        deadline = time.time() + max(1.0, timeout_s)
        actions = 0
        tapped_any = False
        no_candidate_rounds = 0

        while time.time() < deadline and actions < max_actions:
            try:
                fg = self._controller.get_foreground(timeout_s=2.0)
            except Exception:
                fg = None

            if not isinstance(fg, dict) or fg.get("package") != "com.android.chrome":
                if tapped_any:
                    break
                time.sleep(0.5)
                continue

            xml = self._try_dump_uiautomator_xml(timeout_s=10.0)
            if not isinstance(xml, str) or not xml.strip():
                time.sleep(0.5)
                continue

            candidate = self._pick_chrome_first_run_button(xml)
            if candidate is not None:
                x_px, y_px, label = candidate
                try:
                    self._executor.execute(
                        {"type": "tap", "coord": {"x_px": int(x_px), "y_px": int(y_px)}}
                    )
                except Exception:
                    # If tapping fails transiently, keep trying until timeout.
                    time.sleep(0.5)
                    continue
                tapped_any = True
                actions += 1
                no_candidate_rounds = 0
                # Give Chrome time to transition screens.
                time.sleep(0.8)
                continue

            # No candidate found.
            if not tapped_any and self._chrome_first_run_has_progress(xml):
                time.sleep(0.8)
                continue

            no_candidate_rounds += 1
            if no_candidate_rounds >= 3:
                break
            time.sleep(0.5)

        return tapped_any

    def _try_dump_uiautomator_xml(self, *, timeout_s: float) -> Optional[str]:
        try:
            with tempfile.TemporaryDirectory() as td:
                xml_path = Path(td) / "uiautomator.xml"
                self._controller.uiautomator_dump(
                    local_path=xml_path,
                    timeout_s=float(timeout_s),
                )
                return xml_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    def _chrome_first_run_has_progress(self, xml: str) -> bool:
        # Heuristic: Chrome first-run often shows a lone ProgressBar/spinner before buttons appear.
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml)
        except Exception:
            return False

        for el in root.iter():
            if el.tag != "node":
                continue
            cls = str(el.attrib.get("class") or "")
            pkg = str(el.attrib.get("package") or "")
            if pkg == "com.android.chrome" and ("ProgressBar" in cls or "progress" in cls.lower()):
                return True
        return False

    def _pick_chrome_first_run_button(self, xml: str) -> Optional[tuple[int, int, str]]:
        # Prefer safe skip/dismiss options; avoid actions that start sign-in flows.
        allow_patterns: list[tuple[str, int]] = [
            ("use without an account", 100),
            ("continue without an account", 95),
            ("skip", 90),
            ("not now", 85),
            ("no thanks", 80),
            ("accept & continue", 75),
            ("accept and continue", 75),
            ("i agree", 70),
            ("got it", 60),
            ("more", 55),
            ("next", 50),
            ("continue", 45),
            # Common Chinese strings (best-effort; device locale may be en-US).
            ("不使用账号", 100),
            ("不用账号", 100),
            ("跳过", 90),
            ("暂不", 85),
            ("不了，谢谢", 80),
            ("不用了", 80),
            ("接受并继续", 75),
            ("同意并继续", 75),
            ("知道了", 60),
            ("更多", 55),
            ("下一步", 50),
            ("继续", 45),
        ]
        block_patterns = [
            "add account",
            "sign in",
            "login",
            "log in",
            "turn on sync",
            "add to device",
            "添加账号",
            "登录",
            "登入",
        ]

        def parse_bounds(bounds: str) -> Optional[tuple[int, int, int, int]]:
            m = re.match(r"^\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]$", str(bounds).strip())
            if not m:
                return None
            try:
                x1, y1, x2, y2 = (
                    int(m.group(1)),
                    int(m.group(2)),
                    int(m.group(3)),
                    int(m.group(4)),
                )
            except Exception:
                return None
            if x2 <= x1 or y2 <= y1:
                return None
            return (x1, y1, x2, y2)

        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml)
        except Exception:
            return None

        candidates: list[tuple[int, int, int, str]] = []
        for el in root.iter():
            if el.tag != "node":
                continue
            if str(el.attrib.get("clickable") or "") != "true":
                continue
            if str(el.attrib.get("enabled") or "") == "false":
                continue

            text = str(el.attrib.get("text") or "").strip()
            desc = str(el.attrib.get("content-desc") or "").strip()
            label = text or desc
            if not label:
                continue

            label_cf = label.casefold()
            if any(pat in label_cf for pat in block_patterns):
                continue

            score = 0
            for pat, val in allow_patterns:
                if pat in label_cf:
                    score = max(score, int(val))
            if score <= 0:
                continue

            rect = parse_bounds(str(el.attrib.get("bounds") or ""))
            if rect is None:
                continue
            x1, y1, x2, y2 = rect
            x = int((x1 + x2) // 2)
            y = int((y1 + y2) // 2)
            candidates.append((score, y, x, label))

        if not candidates:
            return None

        # Deterministic pick: highest score; if tie, prefer lower on screen.
        candidates.sort(key=lambda t: (-t[0], -t[1], t[2], t[3]))
        _score, _y, _x, label = candidates[0]
        return (_x, _y, label)

    def execute(self, action: Dict[str, Any]) -> Dict[str, Any]:
        return self._executor.execute(action)

    def set_current_obs_digest(self, obs_digest: Optional[str]) -> None:
        self._executor.set_current_obs_digest(obs_digest)
