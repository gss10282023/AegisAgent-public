"""Evidence pack writer for MAS-Harness.

Phase-2 goal: make the episode bundle (logs/traces/obs/oracle evidence) a
first-class, reproducible artifact.

This module intentionally prefers *deterministic* evidence and stable fields.
Screenshots/a11y dumps are included for debugging and post-mortem analysis,
but checkers and task success oracles should primarily rely on hard signals.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from mas_harness.evidence.action_normalizer import MAS_ACTION_SCHEMA_VERSION, normalize_action
from mas_harness.evidence.evidence_pack import ensure_evidence_pack_v0_episode_dir
from mas_harness.evidence.ui_elements import UiElementsExtractor
from mas_harness.oracle_framework.schema_validators import (
    assert_assertion_result_v0,
    assert_fact_v0,
)
from mas_harness.oracle_framework.types import AssertionResult, Fact
from mas_harness.oracles.zoo.base import assert_oracle_event_v0


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps_canonical(obj: Any) -> str:
    """Deterministic JSON encoding for hashing / stable digests."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_sha256(obj: Any) -> str:
    """Compute a stable SHA-256 digest for arbitrary JSON-serializable objects."""
    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)
    else:
        data = _json_dumps_canonical(obj).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def stable_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


_OBS_DIGEST_VERSION = "v3_component_canonicalized"

_NOTIFICATION_TIME_BUCKET_MS = 10 * 60 * 1000
_NOTIFICATION_TEXT_MAX_LEN = 500


def _truncate_text(value: Optional[str], *, max_len: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    if len(value) <= max_len:
        return value
    return value[:max_len]


def _canonicalize_ui_elements(ui_elements: Any) -> list[dict[str, Any]]:
    if not isinstance(ui_elements, list):
        return []
    out: list[dict[str, Any]] = []
    for el in ui_elements:
        if not isinstance(el, dict):
            continue
        bbox = el.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = (_safe_int(v) for v in bbox)
        if None in (x1, y1, x2, y2):
            continue
        canonical: dict[str, Any] = {
            "bbox": [int(x1), int(y1), int(x2), int(y2)],
            "package": str(el.get("package") or ""),
            "resource_id": _nonempty_str(el.get("resource_id")),
            "text": _truncate_text(
                _nonempty_str(el.get("text")), max_len=_NOTIFICATION_TEXT_MAX_LEN
            ),
            "desc": _truncate_text(
                _nonempty_str(el.get("desc")), max_len=_NOTIFICATION_TEXT_MAX_LEN
            ),
            "clickable": bool(el.get("clickable") is True),
        }
        cls = _nonempty_str(el.get("class"))
        if cls is not None:
            canonical["class"] = cls
        for flag in ("enabled", "focused", "selected", "checked", "scrollable"):
            val = el.get(flag)
            if isinstance(val, bool):
                canonical[flag] = bool(val)
        out.append(canonical)

    def _key(item: dict[str, Any]) -> tuple[Any, ...]:
        bbox = item.get("bbox")
        bbox_t = (
            tuple(int(v) for v in bbox)
            if isinstance(bbox, list) and len(bbox) == 4
            else (0, 0, 0, 0)
        )
        return (
            bbox_t,
            str(item.get("package") or ""),
            str(item.get("resource_id") or ""),
            str(item.get("text") or ""),
            str(item.get("desc") or ""),
            str(item.get("class") or ""),
            1 if item.get("clickable") is True else 0,
        )

    out.sort(key=_key)
    return out


def _bucket_epoch_ms(value: Any, *, bucket_ms: int) -> Optional[int]:
    ts = _safe_int(value)
    if ts is None:
        return None
    if ts <= 0:
        return None
    # Heuristic: treat small values as seconds.
    ts_ms = int(ts * 1000) if ts < 10_000_000_000 else int(ts)
    return int((ts_ms // int(bucket_ms)) * int(bucket_ms))


def _canonicalize_notifications(notifications: Any) -> list[dict[str, Any]]:
    if not isinstance(notifications, list):
        return []

    out: list[dict[str, Any]] = []
    for n in notifications:
        if not isinstance(n, dict):
            continue
        pkg = _first_nonempty_str(
            n.get("pkg"),
            n.get("package"),
            n.get("package_name"),
            n.get("packageName"),
            n.get("app_package"),
            n.get("appPackage"),
        )
        title = _truncate_text(
            _first_nonempty_str(
                n.get("title"), n.get("ticker"), n.get("tickerText"), n.get("subject")
            ),
            max_len=_NOTIFICATION_TEXT_MAX_LEN,
        )
        text = _truncate_text(
            _first_nonempty_str(n.get("text"), n.get("content"), n.get("body"), n.get("message")),
            max_len=_NOTIFICATION_TEXT_MAX_LEN,
        )

        posted_bucket_ms = None
        for key in (
            "posted_time_ms",
            "postedTimeMs",
            "posted_time",
            "postedTime",
            "when_ms",
            "when",
            "timestamp_ms",
            "timestamp",
            "time_ms",
            "time",
            "post_time_ms",
            "postTimeMs",
            "post_time",
            "postTime",
        ):
            if key not in n:
                continue
            candidate = _bucket_epoch_ms(n.get(key), bucket_ms=_NOTIFICATION_TIME_BUCKET_MS)
            if candidate is None:
                continue
            posted_bucket_ms = candidate
            break

        out.append(
            {
                "pkg": pkg,
                "title": title,
                "text": text,
                "posted_time_bucket_ms": posted_bucket_ms,
            }
        )

    out.sort(
        key=lambda item: (
            str(item.get("pkg") or ""),
            str(item.get("title") or ""),
            str(item.get("text") or ""),
            int(item.get("posted_time_bucket_ms") or 0),
        )
    )
    return out


def _length_bucket(n: int) -> str:
    if n <= 0:
        return "0"
    if n <= 10:
        return "1-10"
    if n <= 50:
        return "11-50"
    if n <= 200:
        return "51-200"
    if n <= 1000:
        return "201-1000"
    return "1001+"


def _clipboard_bucket(clipboard: Any) -> Optional[dict[str, Any]]:
    if clipboard is None:
        return None

    text: Optional[str] = None
    if isinstance(clipboard, dict):
        text = _first_nonempty_str(
            clipboard.get("text"), clipboard.get("content"), clipboard.get("value")
        )
    elif isinstance(clipboard, str):
        text = clipboard
    else:
        text = str(clipboard)

    text = text or ""
    length = len(text)
    return {
        "present": True,
        "nonempty": bool(text.strip()),
        "length_bucket": _length_bucket(length),
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_TINY_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc````\x00\x00\x00\x05\x00\x01"
    b"\x0d\n\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _png_size_px(png_bytes: bytes) -> tuple[Optional[int], Optional[int]]:
    # Parse PNG IHDR width/height (no external deps).
    if not isinstance(png_bytes, (bytes, bytearray)):
        return None, None
    data = bytes(png_bytes)
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None, None
    try:
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
    except Exception:
        return None, None
    if w <= 0 or h <= 0:
        return None, None
    return w, h


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return int(v)
        return int(v)
    except Exception:
        return None


def _safe_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "t", "1", "yes", "y"}:
            return True
        if s in {"false", "f", "0", "no", "n"}:
            return False
    return None


def _normalize_bbox(v: Any) -> Optional[Tuple[int, int, int, int]]:
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    a, b, c, d = (_safe_int(x) for x in v)
    if None in (a, b, c, d):
        return None
    x1, y1, x2, y2 = int(a), int(b), int(c), int(d)
    if x2 < x1 or y2 < y1:
        return None
    return x1, y1, x2, y2


def _normalize_screen_info(screen_info: Any) -> Dict[str, Any]:
    """Normalize adapter-provided screen info to stable fields.

    Expected inputs (best-effort):
      - AndroidController.get_screen_info() dict (wm_size/wm_density/surface_orientation)
      - direct keys: width_px/height_px/density_dpi/surface_orientation
    """

    width_px: Optional[int] = None
    height_px: Optional[int] = None
    density_dpi: Optional[int] = None
    surface_orientation: Optional[int] = None

    if isinstance(screen_info, dict):
        width_px = _safe_int(screen_info.get("width_px"))
        height_px = _safe_int(screen_info.get("height_px"))
        density_dpi = _safe_int(screen_info.get("density_dpi"))
        surface_orientation = _safe_int(
            screen_info.get("surface_orientation")
            if "surface_orientation" in screen_info
            else screen_info.get("orientation")
        )

        # AndroidController.get_screen_info format
        wm_size = screen_info.get("wm_size") if (width_px is None or height_px is None) else None
        if isinstance(wm_size, dict):
            override = wm_size.get("override_size")
            physical = wm_size.get("physical_size")
            size = override if isinstance(override, (list, tuple)) else physical
            if isinstance(size, (list, tuple)) and len(size) == 2:
                w, h = _safe_int(size[0]), _safe_int(size[1])
                if w and h:
                    width_px, height_px = w, h

        wm_density = screen_info.get("wm_density") if density_dpi is None else None
        if isinstance(wm_density, dict):
            override = _safe_int(wm_density.get("override_density"))
            physical = _safe_int(wm_density.get("physical_density"))
            density_dpi = override if override else physical

        if surface_orientation is None:
            surface_orientation = _safe_int(screen_info.get("surface_orientation"))

    orientation_label: Optional[str] = None
    if surface_orientation in (0, 2):
        orientation_label = "portrait"
    elif surface_orientation in (1, 3):
        orientation_label = "landscape"
    elif width_px is not None and height_px is not None and width_px > 0 and height_px > 0:
        orientation_label = "portrait" if height_px >= width_px else "landscape"

    return {
        "width_px": width_px,
        "height_px": height_px,
        "density_dpi": density_dpi,
        "surface_orientation": surface_orientation,
        "orientation": orientation_label,
    }


def _normalize_screen_size_px(v: Any) -> Optional[Dict[str, int]]:
    if not isinstance(v, dict):
        return None
    w = _safe_int(v.get("w") if "w" in v else v.get("width_px", v.get("width")))
    h = _safe_int(v.get("h") if "h" in v else v.get("height_px", v.get("height")))
    if w is None or h is None or w <= 0 or h <= 0:
        return None
    return {"w": int(w), "h": int(h)}


def _normalize_physical_frame_boundary_px(v: Any) -> Optional[Dict[str, int]]:
    if isinstance(v, dict):
        left = _safe_int(v.get("left"))
        top = _safe_int(v.get("top"))
        right = _safe_int(v.get("right"))
        bottom = _safe_int(v.get("bottom"))
        if None in (left, top, right, bottom):
            return None
        if int(right) < int(left) or int(bottom) < int(top):
            return None
        return {"left": int(left), "top": int(top), "right": int(right), "bottom": int(bottom)}

    bbox = _normalize_bbox(v)
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    return {"left": int(left), "top": int(top), "right": int(right), "bottom": int(bottom)}


def _try_read_json_object(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        return data
    return None


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s if s else None


def _first_nonempty_str(*candidates: Any) -> Optional[str]:
    for candidate in candidates:
        s = _nonempty_str(candidate)
        if s is not None:
            return s
    return None


_ORACLE_DECISION_VALUES = {"pass", "fail", "inconclusive", "not_applicable"}


@dataclass(frozen=True)
class EpisodePaths:
    """Filesystem layout for an evidence bundle."""

    root: Path
    ui_dump_dir: Path
    screenshots_dir: Path
    action_trace: Path
    device_input_trace: Path
    obs_trace: Path
    foreground_trace: Path
    device_trace: Path
    screen_trace: Path
    agent_call_trace: Path
    agent_action_trace: Path
    ui_elements: Path
    oracle_trace: Path
    facts_path: Path
    assertions_path: Path
    summary: Path


class EvidenceWriter:
    """Write an evidence bundle for a single episode."""

    def __init__(
        self,
        run_dir: Path,
        *,
        case_id: str,
        seed: int,
        run_mode: str = "public",
        metadata: Optional[Dict[str, Any]] = None,
        episode_dir: Path | None = None,
        ui_dump_every_n: int = 5,
        ui_elements_max: int = 5000,
    ) -> None:
        self.case_id = case_id
        self.seed = seed
        self.run_mode = run_mode
        self._run_dir = Path(run_dir)

        if episode_dir is not None:
            self.root = Path(episode_dir)
        else:
            # Deterministic subdir so that the same case/seed doesn't overwrite other seeds,
            # while avoiding timestamps that break reproducibility.
            self.root = Path(run_dir) / run_mode / case_id / f"seed_{seed}"
        self.root.mkdir(parents=True, exist_ok=True)

        ensure_evidence_pack_v0_episode_dir(self.root)

        self.ui_dump_dir = self.root / "ui_dump"
        self.screenshots_dir = self.root / "screenshots"

        self.paths = EpisodePaths(
            root=self.root,
            ui_dump_dir=self.ui_dump_dir,
            screenshots_dir=self.screenshots_dir,
            action_trace=self.root / "action_trace.jsonl",
            device_input_trace=self.root / "device_input_trace.jsonl",
            obs_trace=self.root / "obs_trace.jsonl",
            foreground_trace=self.root / "foreground_trace.jsonl",
            device_trace=self.root / "device_trace.jsonl",
            screen_trace=self.root / "screen_trace.jsonl",
            agent_call_trace=self.root / "agent_call_trace.jsonl",
            agent_action_trace=self.root / "agent_action_trace.jsonl",
            ui_elements=self.root / "ui_elements.jsonl",
            oracle_trace=self.root / "oracle_trace.jsonl",
            facts_path=self.root / "facts.jsonl",
            assertions_path=self.root / "assertions.jsonl",
            summary=self.root / "summary.json",
        )

        self._files = {
            "action": self.paths.action_trace.open("w", encoding="utf-8"),
            "device_input": self.paths.device_input_trace.open("w", encoding="utf-8"),
            "obs": self.paths.obs_trace.open("w", encoding="utf-8"),
            "foreground": self.paths.foreground_trace.open("w", encoding="utf-8"),
            "device": self.paths.device_trace.open("w", encoding="utf-8"),
            "screen": self.paths.screen_trace.open("w", encoding="utf-8"),
            "agent_call": self.paths.agent_call_trace.open("w", encoding="utf-8"),
            "agent_action": self.paths.agent_action_trace.open("w", encoding="utf-8"),
            "ui_elements": self.paths.ui_elements.open("w", encoding="utf-8"),
            "oracle": self.paths.oracle_trace.open("w", encoding="utf-8"),
            # Phase4: audit-first artifacts (optional; created empty to avoid stale reuse).
            "facts": self.paths.facts_path.open("w", encoding="utf-8"),
            "assertions": self.paths.assertions_path.open("w", encoding="utf-8"),
        }

        self._start_ms = _utc_ms()
        self._meta = metadata or {}
        self._ui_dump_every_n = int(ui_dump_every_n) if int(ui_dump_every_n) > 0 else 0
        self._ui_extractor = UiElementsExtractor(max_elements=ui_elements_max)
        self._wrote_uiautomator_xml = False
        self._last_screen: Dict[str, Any] | None = None
        self._last_obs_digest: Optional[str] = None
        # Phase3 audit_only downgrade: before any observation is recorded, ref checks are not
        # applicable.
        self._auditability_limited: bool = True
        self._auditability_limits: list[str] = ["no_screenshot", "no_geometry"]
        self._last_device_input_step_idx: int | None = None

        # Write an initial header to make bundles self-describing.
        header = {
            "event": "episode_start",
            "ts_ms": self._start_ms,
            "case_id": case_id,
            "seed": seed,
            "run_mode": run_mode,
            "metadata": self._meta,
        }
        self._write_line("obs", header)

    # -----------------
    # Low-level helpers
    # -----------------
    def _write_line(self, stream: str, obj: Dict[str, Any]) -> None:
        f = self._files[stream]
        f.write(_json_dumps_canonical(obj))
        f.write("\n")
        f.flush()

    def close(self) -> None:
        # Best-effort: ensure at least one UIAutomator XML exists per episode bundle.
        # If the adapter did not provide it during observations, leave a placeholder
        # so downstream tooling can rely on the filesystem layout.
        if not self._wrote_uiautomator_xml:
            try:
                placeholder = self.ui_dump_dir / "uiautomator_placeholder.xml"
                if not placeholder.exists():
                    placeholder.write_text(
                        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
                        '<hierarchy rotation="0"></hierarchy>\n',
                        encoding="utf-8",
                    )
            except Exception:
                pass
        for f in self._files.values():
            try:
                f.close()
            except Exception:
                # Best-effort close.
                pass

    @property
    def last_obs_digest(self) -> Optional[str]:
        return self._last_obs_digest

    # -----------------
    # Evidence recording
    # -----------------
    def record_observation(self, step: int, observation: Dict[str, Any]) -> None:
        """Record observation + derived traces.

        Expected (optional) keys in observation:
          - screenshot_png: bytes
          - a11y_tree: dict
          - screen_info: dict
          - ui_hash: str
          - foreground: {package, activity}
          - notifications: list
          - clipboard: {text, op}
        """
        ts_ms = _utc_ms()

        screenshot_file = None
        a11y_file = None

        raw_screenshot_bytes = observation.get("screenshot_png")
        screenshot_provided = (
            isinstance(raw_screenshot_bytes, (bytes, bytearray)) and len(raw_screenshot_bytes) > 0
        )

        screenshot_bytes = raw_screenshot_bytes if screenshot_provided else None
        if screenshot_bytes is None:
            # Keep the structure stable even if an adapter cannot provide screenshots.
            screenshot_bytes = _TINY_PNG_1X1

        screenshot_file = f"screenshot_step_{step:04d}.png"
        screenshot_path = self.screenshots_dir / screenshot_file
        screenshot_path.write_bytes(screenshot_bytes)

        a11y_tree = observation.get("a11y_tree")
        if a11y_tree is not None:
            a11y_file = f"a11y_step_{step:04d}.json"
            a11y_path = self.ui_dump_dir / a11y_file
            a11y_path.write_text(_json_dumps_canonical(a11y_tree), encoding="utf-8")

        ui_hash = observation.get("ui_hash")
        if ui_hash is None:
            # Derive a hash from available fields to make loop detection possible.
            ui_hash = stable_sha256(
                {
                    "foreground": observation.get("foreground"),
                    "a11y": a11y_tree,
                }
            )

        screenshot_sha256 = stable_file_sha256(screenshot_path)
        a11y_sha256 = stable_file_sha256(self.ui_dump_dir / a11y_file) if a11y_file else None

        # screen trace: resolution/density/orientation + Phase3 Coord-B geometry fields.
        screen_info = observation.get("screen_info") or observation.get("screen")
        screen_payload = _normalize_screen_info(screen_info)

        screenshot_size_px = _normalize_screen_size_px(observation.get("screenshot_size_px"))
        logical_screen_size_px = _normalize_screen_size_px(
            observation.get("logical_screen_size_px")
        )
        physical_frame_boundary_px = _normalize_physical_frame_boundary_px(
            observation.get("physical_frame_boundary_px")
        )

        geometry_provided = (
            screenshot_size_px is not None
            or logical_screen_size_px is not None
            or physical_frame_boundary_px is not None
            or (
                isinstance(screen_payload.get("width_px"), int)
                and isinstance(screen_payload.get("height_px"), int)
                and int(screen_payload.get("width_px") or 0) > 0
                and int(screen_payload.get("height_px") or 0) > 0
            )
        )
        geometry_available = geometry_provided or screenshot_provided

        auditability_limits: list[str] = []
        if not screenshot_provided:
            auditability_limits.append("no_screenshot")
        if not geometry_available:
            auditability_limits.append("no_geometry")
        self._auditability_limits = auditability_limits
        self._auditability_limited = bool(auditability_limits)

        if screenshot_size_px is None:
            w_s, h_s = _png_size_px(screenshot_bytes)
            if w_s is not None and h_s is not None:
                screenshot_size_px = {"w": int(w_s), "h": int(h_s)}

        if logical_screen_size_px is None:
            w = _safe_int(screen_payload.get("width_px"))
            h = _safe_int(screen_payload.get("height_px"))
            if w is None or h is None:
                w = screenshot_size_px.get("w") if screenshot_size_px else None
                h = screenshot_size_px.get("h") if screenshot_size_px else None
            if w is not None and h is not None:
                logical_screen_size_px = {"w": int(w), "h": int(h)}

        if physical_frame_boundary_px is None:
            w = logical_screen_size_px.get("w") if logical_screen_size_px else None
            h = logical_screen_size_px.get("h") if logical_screen_size_px else None
            if w is None or h is None:
                w = screenshot_size_px.get("w") if screenshot_size_px else None
                h = screenshot_size_px.get("h") if screenshot_size_px else None
            physical_frame_boundary_px = {
                "left": 0,
                "top": 0,
                "right": int(w or 0),
                "bottom": int(h or 0),
            }

        orientation = screen_payload.get("orientation")
        if orientation is None and logical_screen_size_px is not None:
            w = logical_screen_size_px.get("w")
            h = logical_screen_size_px.get("h")
            if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                orientation = "portrait" if h >= w else "landscape"

        screen_payload_v2 = {
            **screen_payload,
            "orientation": orientation,
            "screenshot_size_px": screenshot_size_px,
            "logical_screen_size_px": logical_screen_size_px,
            "physical_frame_boundary_px": physical_frame_boundary_px,
        }
        self._last_screen = screen_payload_v2

        fg = observation.get("foreground") or {}
        if not fg:
            fg = {
                "package": observation.get("foreground_package"),
                "activity": observation.get("foreground_activity"),
            }
        default_package = fg.get("package")
        fg_package = fg.get("package")
        fg_activity = fg.get("activity")

        screenshot_digest = screenshot_sha256 if screenshot_provided else None
        foreground_digest = _sha256_text(f"{fg_package or ''}{fg_activity or ''}")
        geometry_payload = {
            "screenshot_size_px": screenshot_size_px,
            "logical_screen_size_px": logical_screen_size_px,
            "physical_frame_boundary_px": physical_frame_boundary_px,
            "orientation": orientation,
        }
        geometry_digest = stable_sha256(geometry_payload) if geometry_available else None

        uiautomator_xml = observation.get("uiautomator_xml")
        if uiautomator_xml is None:
            uiautomator_xml = observation.get("uiautomator_xml_bytes")
        extracted = self._ui_extractor.extract(
            a11y_tree=a11y_tree if isinstance(a11y_tree, dict) else None,
            uiautomator_xml=uiautomator_xml,
            default_package=default_package if isinstance(default_package, str) else None,
        )
        canonical_ui_elements = _canonicalize_ui_elements(extracted.ui_elements)
        ui_elements_digest = stable_sha256(canonical_ui_elements) if canonical_ui_elements else None
        rotation = _safe_int(screen_payload.get("surface_orientation"))
        ui_dump_digest = (
            _sha256_text(
                self._ui_extractor.synthesize_uiautomator_xml(
                    ui_elements=canonical_ui_elements,
                    rotation=rotation if isinstance(rotation, int) else None,
                )
            )
            if canonical_ui_elements
            else None
        )

        notifications = observation.get("notifications") or []
        notifications_digest = None
        canonical_notifications = _canonicalize_notifications(notifications)
        if canonical_notifications:
            notifications_digest = stable_sha256(canonical_notifications)

        clipboard = observation.get("clipboard")
        clipboard_bucket = _clipboard_bucket(clipboard)
        clipboard_digest = stable_sha256(clipboard_bucket) if clipboard_bucket is not None else None

        obs_component_digests = {
            "screenshot_digest": screenshot_digest,
            "ui_dump_digest": ui_dump_digest,
            "ui_elements_digest": ui_elements_digest,
            "foreground_digest": foreground_digest,
            "geometry_digest": geometry_digest,
            "notifications_digest": notifications_digest,
            "clipboard_digest": clipboard_digest,
        }
        if None in (screenshot_digest, foreground_digest, geometry_digest):
            obs_digest = None
        else:
            obs_digest = stable_sha256(
                {"version": _OBS_DIGEST_VERSION, "components": obs_component_digests}
            )
        self._last_obs_digest = obs_digest

        # obs trace: file pointers + hashes (for auditability)
        self._write_line(
            "obs",
            {
                "event": "observation",
                "ts_ms": ts_ms,
                "step": step,
                "screenshot_file": f"screenshots/{screenshot_file}",
                "screenshot_sha256": screenshot_sha256,
                "a11y_file": (f"ui_dump/{a11y_file}" if a11y_file else None),
                "a11y_sha256": a11y_sha256,
                "ui_hash": ui_hash,
                "obs_digest": obs_digest,
                "obs_digest_version": _OBS_DIGEST_VERSION,
                "obs_component_digests": obs_component_digests,
            },
        )

        self._write_line(
            "screen",
            {
                "event": "screen",
                "ts_ms": ts_ms,
                "step": step,
                **screen_payload_v2,
            },
        )

        # foreground trace
        self._write_line(
            "foreground",
            {
                "event": "foreground",
                "ts_ms": ts_ms,
                "step": step,
                "package": fg.get("package"),
                "activity": fg.get("activity"),
            },
        )

        notifications_count = len(notifications) if isinstance(notifications, list) else None
        self._write_line(
            "device",
            {
                "event": "device",
                "ts_ms": ts_ms,
                "step": step,
                "notifications_count": notifications_count,
                "clipboard_present": clipboard is not None,
            },
        )

        should_dump_ui = step == 0 or (self._ui_dump_every_n and step % self._ui_dump_every_n == 0)

        uiautomator_xml_file = None
        uiautomator_xml_sha256 = None

        uiautomator_xml = observation.get("uiautomator_xml")
        if uiautomator_xml is None:
            uiautomator_xml = observation.get("uiautomator_xml_bytes")

        if should_dump_ui:
            if uiautomator_xml is None:
                # Fallback: synthesize a minimal UIAutomator-like XML from a11y-derived elements.
                rotation = _normalize_screen_info(screen_info).get("surface_orientation")
                extracted = self._ui_extractor.extract(
                    a11y_tree=a11y_tree if isinstance(a11y_tree, dict) else None,
                    uiautomator_xml=None,
                    default_package=default_package,
                )
                uiautomator_xml = self._ui_extractor.synthesize_uiautomator_xml(
                    ui_elements=extracted.ui_elements,
                    rotation=rotation if isinstance(rotation, int) else None,
                )

            uiautomator_xml_file = f"uiautomator_step_{step:04d}.xml"
            uiautomator_path = self.ui_dump_dir / uiautomator_xml_file
            if isinstance(uiautomator_xml, (bytes, bytearray)):
                uiautomator_path.write_bytes(bytes(uiautomator_xml))
            else:
                uiautomator_path.write_text(str(uiautomator_xml), encoding="utf-8")
            uiautomator_xml_sha256 = stable_file_sha256(uiautomator_path)
            self._wrote_uiautomator_xml = True

            extracted = self._ui_extractor.extract(
                a11y_tree=a11y_tree if isinstance(a11y_tree, dict) else None,
                uiautomator_xml=uiautomator_xml,
                default_package=default_package,
            )
            ui_elements = extracted.ui_elements
            self._write_line(
                "ui_elements",
                {
                    "event": "ui_elements",
                    "ts_ms": ts_ms,
                    "step": step,
                    "ui_hash": ui_hash,
                    "source": extracted.source,
                    "ui_elements": ui_elements,
                    "elements_count": len(ui_elements),
                    "elements_sha256": stable_sha256(ui_elements),
                    "a11y_file": (f"ui_dump/{a11y_file}" if a11y_file else None),
                    "a11y_sha256": a11y_sha256,
                    "uiautomator_xml_file": (
                        f"ui_dump/{uiautomator_xml_file}" if uiautomator_xml_file else None
                    ),
                    "uiautomator_xml_sha256": uiautomator_xml_sha256,
                    "errors": extracted.errors or None,
                },
            )
        else:
            nodes = None
            if isinstance(a11y_tree, dict):
                nodes = a11y_tree.get("nodes")
            self._write_line(
                "ui_elements",
                {
                    "event": "ui_elements_summary",
                    "ts_ms": ts_ms,
                    "step": step,
                    "ui_hash": ui_hash,
                    "a11y_file": (f"ui_dump/{a11y_file}" if a11y_file else None),
                    "a11y_sha256": a11y_sha256,
                    "elements_count": len(nodes) if isinstance(nodes, list) else None,
                },
            )

    def record_action(self, step: int, action: Dict[str, Any], result: Dict[str, Any]) -> None:
        self._write_line(
            "action",
            {
                "event": "action",
                "ts_ms": _utc_ms(),
                "step": step,
                "action": action,
                "result": result,
            },
        )

    def record_device_input_event(
        self,
        step_idx: Any,
        ref_step_idx: Any,
        source_level: str,
        event_type: str,
        payload: Dict[str, Any],
        timestamp_ms: Any | None = None,
        mapping_warnings: Any | None = None,
    ) -> None:
        """Write one device input event into device_input_trace.jsonl.

        Phase3 index contract (3.4.3.0a):
          - step_idx is the event index in this file: strictly increasing & unique.
          - L0: ref_step_idx is required and must equal step_idx (1:1 with action step_idx).
          - L1/L2: ref_step_idx may be null or repeated (1:N), but step_idx stays monotonic.
        """

        if not isinstance(source_level, str) or source_level.strip() not in {"L0", "L1", "L2"}:
            raise ValueError("device_input_trace.source_level must be one of: L0, L1, L2")
        level = source_level.strip()

        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("device_input_trace.event_type must be a non-empty string")

        if not isinstance(payload, dict):
            raise ValueError("device_input_trace.payload must be a JSON object")

        def _coerce_int(value: Any) -> int | None:
            if value is None or isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                s = value.strip()
                if not s:
                    return None
                try:
                    return int(s)
                except Exception:
                    return None
            return None

        step_idx_int = _coerce_int(step_idx)
        if step_idx_int is None:
            raise ValueError("device_input_trace.step_idx must be an int")

        last_step_idx = self._last_device_input_step_idx
        if last_step_idx is not None and step_idx_int <= last_step_idx:
            raise ValueError("device_input_trace.step_idx must be strictly increasing")

        ref_step_idx_int: int | None
        if ref_step_idx is None:
            ref_step_idx_int = None
        else:
            ref_step_idx_int = _coerce_int(ref_step_idx)
            if ref_step_idx_int is None:
                raise ValueError("device_input_trace.ref_step_idx must be an int or null")

        if level == "L0":
            if ref_step_idx_int is None:
                raise ValueError("device_input_trace.ref_step_idx is required for L0")
            if ref_step_idx_int != step_idx_int:
                raise ValueError("device_input_trace.ref_step_idx must equal step_idx for L0")

        ts_ms = _coerce_int(timestamp_ms) if timestamp_ms is not None else _utc_ms()
        if ts_ms is None:
            raise ValueError("device_input_trace.timestamp_ms must be an int")

        warnings_out: list[str] = []
        if mapping_warnings is None:
            warnings_out = []
        elif isinstance(mapping_warnings, list):
            for w in mapping_warnings:
                if not isinstance(w, str):
                    raise ValueError(
                        "device_input_trace.mapping_warnings must be a list of strings"
                    )
                if w.strip():
                    warnings_out.append(w)
        else:
            raise ValueError("device_input_trace.mapping_warnings must be a list")

        event_type_norm = event_type.strip().lower()
        payload_out = dict(payload)

        def _require_coord_space_physical_px() -> None:
            coord_space = payload_out.get("coord_space")
            if not isinstance(coord_space, str) or coord_space.strip() != "physical_px":
                raise ValueError("device_input_trace.payload.coord_space must be 'physical_px'")
            payload_out["coord_space"] = "physical_px"

        def _require_no_mapping_warnings_for_l0() -> None:
            if warnings_out:
                raise ValueError(
                    "device_input_trace.mapping_warnings must be empty for L0 coordinate events"
                )

        def _require_coord_unresolved_warning() -> None:
            if "coord_unresolved" not in set(warnings_out):
                raise ValueError(
                    "device_input_trace.mapping_warnings must include 'coord_unresolved'"
                )

        def _forbid_coord_unresolved_warning_if_resolved() -> None:
            if "coord_unresolved" in set(warnings_out):
                raise ValueError(
                    "device_input_trace.mapping_warnings includes 'coord_unresolved' "
                    "but coord is present"
                )

        def _coerce_coord_int(value: Any) -> int | None:
            if value is None or isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                s = value.strip()
                if not s:
                    return None
                try:
                    return int(s)
                except Exception:
                    return None
            return None

        if event_type_norm in {"tap", "long_press"}:
            _require_coord_space_physical_px()
            x_raw = payload_out.get("x") if "x" in payload_out else payload_out.get("x_px")
            y_raw = payload_out.get("y") if "y" in payload_out else payload_out.get("y_px")
            x_int = _coerce_coord_int(x_raw)
            y_int = _coerce_coord_int(y_raw)
            coord_unresolved = x_int is None or y_int is None
            if coord_unresolved:
                payload_out["x"] = None
                payload_out["y"] = None
            else:
                payload_out["x"] = int(x_int)
                payload_out["y"] = int(y_int)

            if level == "L0":
                if coord_unresolved:
                    raise ValueError(
                        "device_input_trace.payload.x/y must be int for L0 coordinate events"
                    )
                _require_no_mapping_warnings_for_l0()
            else:
                if coord_unresolved:
                    _require_coord_unresolved_warning()
                else:
                    _forbid_coord_unresolved_warning_if_resolved()

        if event_type_norm == "swipe":
            _require_coord_space_physical_px()
            start_obj = payload_out.get("start")
            end_obj = payload_out.get("end")
            start: dict[str, Any] = dict(start_obj) if isinstance(start_obj, dict) else {}
            end: dict[str, Any] = dict(end_obj) if isinstance(end_obj, dict) else {}

            sx_raw = start.get("x") if "x" in start else start.get("x_px")
            sy_raw = start.get("y") if "y" in start else start.get("y_px")
            ex_raw = end.get("x") if "x" in end else end.get("x_px")
            ey_raw = end.get("y") if "y" in end else end.get("y_px")

            sx_int = _coerce_coord_int(sx_raw)
            sy_int = _coerce_coord_int(sy_raw)
            ex_int = _coerce_coord_int(ex_raw)
            ey_int = _coerce_coord_int(ey_raw)
            coord_unresolved = None in (sx_int, sy_int, ex_int, ey_int)
            if coord_unresolved:
                payload_out["start"] = {"x": None, "y": None}
                payload_out["end"] = {"x": None, "y": None}
            else:
                payload_out["start"] = {"x": int(sx_int), "y": int(sy_int)}
                payload_out["end"] = {"x": int(ex_int), "y": int(ey_int)}

            if level == "L0":
                if coord_unresolved:
                    raise ValueError(
                        "device_input_trace.payload.start/end must be int for L0 swipe events"
                    )
                _require_no_mapping_warnings_for_l0()
            else:
                if coord_unresolved:
                    _require_coord_unresolved_warning()
                else:
                    _forbid_coord_unresolved_warning_if_resolved()

        self._write_line(
            "device_input",
            {
                "step_idx": step_idx_int,
                "ref_step_idx": ref_step_idx_int,
                "source_level": level,
                "event_type": event_type.strip(),
                "payload": payload_out,
                "timestamp_ms": ts_ms,
                "mapping_warnings": warnings_out,
            },
        )
        self._last_device_input_step_idx = step_idx_int

    def record_reset(self, reset_event: Dict[str, Any]) -> None:
        """Record a reset/snapshot operation.

        This is written into device_trace.jsonl so reset evidence stays inside the
        episode bundle without introducing new required files.
        """
        self._write_line("device", {"event": "reset", "ts_ms": _utc_ms(), **reset_event})

    def record_device_event(self, device_event: Dict[str, Any]) -> None:
        """Write an arbitrary device-scoped event into device_trace.jsonl."""

        payload = dict(device_event)
        payload.setdefault("event", "device_event")
        payload["ts_ms"] = _utc_ms()
        self._write_line("device", payload)

    def record_agent_call(self, call_event: Dict[str, Any]) -> None:
        step_idx = _safe_int(
            call_event.get("step_idx")
            if "step_idx" in call_event
            else call_event.get("step", call_event.get("step_index"))
        )
        tokens_in = _safe_int(call_event.get("tokens_in"))
        tokens_out = _safe_int(call_event.get("tokens_out"))
        latency_ms = _safe_int(call_event.get("latency_ms"))

        agent_name = call_event.get("agent_name") or call_event.get("agent")
        if not isinstance(agent_name, str) or not agent_name.strip():
            agent_name = "unknown"

        input_digest = call_event.get("input_digest")
        if not isinstance(input_digest, str):
            input_digest = None

        response_digest = call_event.get("response_digest")
        if not isinstance(response_digest, str):
            response_digest = None

        provider = call_event.get("provider")
        model_id = call_event.get("model_id")
        base_url = call_event.get("base_url")

        error = call_event.get("error")
        if error is not None and not isinstance(error, (str, dict, list, int, float, bool)):
            error = repr(error)

        # Evidence Pack v3.1 requires a stable set of keys even for toy agents.
        self._write_line(
            "agent_call",
            {
                "event": "agent_call",
                "ts_ms": _utc_ms(),
                "step": step_idx,
                "step_idx": step_idx,
                "agent_name": agent_name,
                "provider": provider,
                "model_id": model_id,
                "base_url": base_url,
                "input_digest": input_digest,
                "response_digest": response_digest,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "error": error,
            },
        )

    def record_tool_call(self, tool_event: Dict[str, Any]) -> None:
        """Backward-compatible alias for Phase-0 naming."""
        self.record_agent_call(tool_event)

    def record_agent_action(self, step: int, action: Dict[str, Any]) -> Dict[str, Any]:
        raw_action: Dict[str, Any] = dict(action) if isinstance(action, dict) else {"raw": action}
        try:
            ref_check_applicable = self._last_obs_digest is not None
            normalized_action, warnings = normalize_action(
                raw_action,
                screen=self._last_screen,
                screen_step=step,
                ref_obs_digest=self._last_obs_digest,
                ref_check_applicable=ref_check_applicable,
            )
        except Exception as e:  # pragma: no cover
            normalized_action = {"type": "unknown", "meta": {"normalizer_error": repr(e)}}
            warnings = [f"normalizer_error:{type(e).__name__}"]

        normalized_action.setdefault("obs_digest", self._last_obs_digest)
        normalized_action.setdefault("auditability_limited", self._auditability_limited)
        if self._auditability_limits:
            normalized_action.setdefault("auditability_limits", list(self._auditability_limits))
        normalized_action.setdefault("step_idx", int(step))

        self._write_line(
            "agent_action",
            {
                "event": "agent_action",
                "ts_ms": _utc_ms(),
                "step": step,
                "step_idx": step,
                "action_schema_version": MAS_ACTION_SCHEMA_VERSION,
                # Backward compatibility: keep `action` while introducing v3.1 fields.
                "action": raw_action,
                "raw_action": raw_action,
                "normalized_action": normalized_action,
                "normalization_warnings": warnings or [],
            },
        )
        return normalized_action

    def record_ui_event(self, ui_event: Dict[str, Any]) -> None:
        """Phase-2 naming: UI events flow into ui_elements.jsonl for now."""
        self._write_line("ui_elements", {"event": "ui_event", "ts_ms": _utc_ms(), **ui_event})

    def record_oracle_events(self, events: Iterable[Dict[str, Any]]) -> None:
        for e in events:
            assert_oracle_event_v0(e)
            self._write_line("oracle", {"event": "oracle", "ts_ms": _utc_ms(), **e})

    def record_oracle_event(self, event: Dict[str, Any]) -> None:
        """Convenience wrapper for a single oracle event."""
        self.record_oracle_events([event])

    def write_fact(self, fact: Dict[str, Any] | Fact) -> Dict[str, Any]:
        obj = fact.to_dict() if isinstance(fact, Fact) else dict(fact)
        assert_fact_v0(obj)
        self._write_line("facts", obj)
        return obj

    def write_assertion_result(self, result: Dict[str, Any] | AssertionResult) -> Dict[str, Any]:
        obj = result.to_dict() if isinstance(result, AssertionResult) else dict(result)
        assert_assertion_result_v0(obj)
        self._write_line("assertions", obj)
        return obj

    def write_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Write the final summary.json.

        The summary should include task success verdict and failure class.
        """
        run_manifest = _try_read_json_object(self._run_dir / "run_manifest.json") or {}
        run_manifest_agent = run_manifest.get("agent")
        run_manifest_agent_name = (
            run_manifest_agent.get("agent_name") if isinstance(run_manifest_agent, dict) else None
        )

        phase0_meta = self._meta.get("phase0") if isinstance(self._meta, dict) else None
        phase0_agent_name = phase0_meta.get("agent_name") if isinstance(phase0_meta, dict) else None
        phase0_execution_mode = (
            phase0_meta.get("execution_mode") if isinstance(phase0_meta, dict) else None
        )

        phase3_fields: Dict[str, Any] = {
            "agent_id": _first_nonempty_str(
                self._meta.get("agent_id") if isinstance(self._meta, dict) else None,
                run_manifest_agent_name,
                phase0_agent_name,
            ),
            "availability": run_manifest.get("availability"),
            "execution_mode": run_manifest.get("execution_mode") or phase0_execution_mode,
            "action_trace_level": run_manifest.get("action_trace_level"),
            "action_trace_source": run_manifest.get("action_trace_source"),
            "eval_mode": run_manifest.get("eval_mode"),
            "guard_enforced": run_manifest.get("guard_enforced"),
            "guard_unenforced_reason": run_manifest.get("guard_unenforced_reason"),
            "guard_enforcement": run_manifest.get("guard_enforcement"),
            "env_profile": run_manifest.get("env_profile"),
            "evidence_trust_level": run_manifest.get("evidence_trust_level"),
            "oracle_source": run_manifest.get("oracle_source"),
            "run_purpose": run_manifest.get("run_purpose"),
        }

        # Backward compatibility: older runners wrote task_success as an object.
        summary_out = dict(summary) if isinstance(summary, dict) else {}
        task_success_details = summary_out.get("task_success_details")
        if not isinstance(task_success_details, dict):
            task_success_details = None

        old_task_success = summary_out.get("task_success")
        if isinstance(old_task_success, dict):
            if task_success_details is None:
                task_success_details = dict(old_task_success)
            summary_out.pop("task_success", None)

        if task_success_details is not None:
            summary_out["task_success_details"] = task_success_details

        end_ms = _utc_ms()
        merged: Dict[str, Any] = {
            "case_id": self.case_id,
            "seed": self.seed,
            "run_mode": self.run_mode,
            "started_ts_ms": self._start_ms,
            "ended_ts_ms": end_ms,
            "duration_ms": end_ms - self._start_ms,
            "metadata": self._meta,
            **phase3_fields,
            **summary_out,
        }

        # Phase-3 semantic fields (C): oracle_decision / agent_reported_finished / task_success.
        oracle_decision = merged.get("oracle_decision")
        if isinstance(oracle_decision, str):
            oracle_decision = oracle_decision.strip()
        if oracle_decision not in _ORACLE_DECISION_VALUES:
            oracle_decision = None

        if oracle_decision is None:
            run_purpose = merged.get("run_purpose")
            if run_purpose in {"agentctl_nl", "ingest_only"}:
                oracle_decision = "not_applicable"
            else:
                details = merged.get("task_success_details")
                if isinstance(details, dict):
                    conclusive = details.get("conclusive")
                    success = details.get("success")
                    if conclusive is True and success is True:
                        oracle_decision = "pass"
                    elif conclusive is True and success is False:
                        oracle_decision = "fail"
                    else:
                        oracle_decision = "inconclusive"
                else:
                    status = merged.get("status")
                    if status == "success":
                        oracle_decision = "pass"
                    elif status == "fail":
                        oracle_decision = "fail"
                    else:
                        oracle_decision = "inconclusive"

        agent_reported_finished = merged.get("agent_reported_finished")
        if not isinstance(agent_reported_finished, bool):
            terminated_reason = merged.get("terminated_reason")
            agent_reported_finished = terminated_reason in {"agent_stop"}

        if oracle_decision == "pass":
            task_success: Any = True
        elif oracle_decision == "fail":
            task_success = False
        else:
            task_success = "unknown"

        merged["oracle_decision"] = oracle_decision
        merged["agent_reported_finished"] = bool(agent_reported_finished)
        merged["task_success"] = task_success

        self.paths.summary.write_text(_json_dumps_canonical(merged), encoding="utf-8")
        return merged
