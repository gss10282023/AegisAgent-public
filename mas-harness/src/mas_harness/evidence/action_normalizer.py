"""MAS Action Normalizer (Phase 2 Step 14).

This module converts heterogeneous agent/tool action outputs into a stable
"MAS Action" schema that is easy to audit and validate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Tuple

import jsonschema

MAS_ACTION_SCHEMA_VERSION = "3.1"


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return int(v)
        return int(v)
    except Exception:
        return None


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return float(int(v))
        return float(v)
    except Exception:
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


@dataclass(frozen=True)
class ScreenSize:
    width_px: int
    height_px: int

    @classmethod
    def from_any(cls, screen: Any) -> Optional["ScreenSize"]:
        if not isinstance(screen, Mapping):
            return None
        w = _safe_int(screen.get("width_px"))
        h = _safe_int(screen.get("height_px"))
        if w is None or h is None or w <= 0 or h <= 0:
            return None
        return cls(width_px=w, height_px=h)


@dataclass(frozen=True)
class FrameBoundary:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width_px(self) -> int:
        return int(self.right - self.left)

    @property
    def height_px(self) -> int:
        return int(self.bottom - self.top)

    @classmethod
    def from_any(cls, v: Any) -> Optional["FrameBoundary"]:
        if not isinstance(v, Mapping):
            return None
        left = _safe_int(v.get("left"))
        top = _safe_int(v.get("top"))
        right = _safe_int(v.get("right"))
        bottom = _safe_int(v.get("bottom"))
        if None in (left, top, right, bottom):
            return None
        if int(right) <= int(left) or int(bottom) <= int(top):
            return None
        return cls(left=int(left), top=int(top), right=int(right), bottom=int(bottom))


# =========================================================
# Coordinate mapping: render/screenshot_px -> device/window_px
# =========================================================
@dataclass
class CoordinateMapper:
    """Ported from `mobile_agent/ui_tars_7b_kit/action_executor.py`.

    Maps coordinates from a render image into a target device/window coordinate
    system via:
      render_px -> (valid_rect + rotation) -> normalized -> device_px
    """

    render_w: int
    render_h: int
    device_w: int
    device_h: int
    # Valid region in render image (remove black bars/crop), (vx, vy, vw, vh).
    # If vw/vh<=0, treat the entire render image as valid.
    valid_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
    # Rotation of render relative to device in degrees: 0/90/180/270
    rotation: int = 0

    def to_device(self, pt: Tuple[float, float]) -> Tuple[int, int]:
        x, y = pt
        vx, vy, vw, vh = self.valid_rect
        if vw <= 0 or vh <= 0:
            vx, vy, vw, vh = 0, 0, self.render_w, self.render_h

        nx = (x - vx) / float(vw)
        ny = (y - vy) / float(vh)

        r = self.rotation % 360
        if r == 90:
            nx, ny = ny, 1 - nx
        elif r == 180:
            nx, ny = 1 - nx, 1 - ny
        elif r == 270:
            nx, ny = 1 - ny, nx

        X = int(round(nx * self.device_w))
        Y = int(round(ny * self.device_h))
        X = max(0, min(self.device_w - 1, X))
        Y = max(0, min(self.device_h - 1, Y))
        return X, Y

    def to_device_with_trace(
        self, pt: Tuple[float, float]
    ) -> Tuple[Tuple[int, int], List[Dict[str, Any]]]:
        x, y = pt
        vx, vy, vw, vh = self.valid_rect
        if vw <= 0 or vh <= 0:
            vx, vy, vw, vh = 0, 0, self.render_w, self.render_h

        trace: List[Dict[str, Any]] = []
        trace.append(
            {
                "step": "input",
                "render_xy": (float(x), float(y)),
                "render_size": (int(self.render_w), int(self.render_h)),
                "device_size": (int(self.device_w), int(self.device_h)),
                "valid_rect": (int(vx), int(vy), int(vw), int(vh)),
                "rotation": int(self.rotation % 360),
            }
        )

        nx = (x - vx) / float(vw)
        ny = (y - vy) / float(vh)
        trace.append({"step": "normalize", "nx": float(nx), "ny": float(ny)})

        r = self.rotation % 360
        if r == 90:
            nx, ny = ny, 1 - nx
        elif r == 180:
            nx, ny = 1 - nx, 1 - ny
        elif r == 270:
            nx, ny = 1 - ny, nx
        trace.append({"step": "rotate", "rotation": int(r), "nx": float(nx), "ny": float(ny)})

        xf = nx * self.device_w
        yf = ny * self.device_h
        trace.append({"step": "scale", "x_f": float(xf), "y_f": float(yf)})

        x_unclamped = int(round(xf))
        y_unclamped = int(round(yf))
        trace.append({"step": "round", "x": int(x_unclamped), "y": int(y_unclamped)})

        x_clamped = max(0, min(self.device_w - 1, x_unclamped))
        y_clamped = max(0, min(self.device_h - 1, y_unclamped))
        trace.append({"step": "clamp", "x": int(x_clamped), "y": int(y_clamped)})

        return (x_clamped, y_clamped), trace


ActionDict = Dict[str, Any]
PointSpec = Tuple[Optional[float], Optional[float], str]
SwipePoints = Tuple[PointSpec, PointSpec]

MAS_ACTION_TYPES_V3_1 = {
    "tap",
    "long_press",
    "swipe",
    "type",
    "press_back",
    "home",
    "open_app",
    "open_url",
    "wait",
    "finished",
    "unknown",
}


MAS_ACTION_SCHEMA_V1: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {"type": "string", "minLength": 1},
        # Coord-A: normalized coordinate actions carry an explicit coord_space so
        # downstream executors have a single canonical interpretation.
        "coord_space": {"type": ["string", "null"], "minLength": 1},
        "coord": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "x_px": {"type": ["integer", "null"], "minimum": 0},
                "y_px": {"type": ["integer", "null"], "minimum": 0},
                "x_norm": {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0},
                "y_norm": {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0},
            },
        },
        "start": {"$ref": "#/$defs/coord"},
        "end": {"$ref": "#/$defs/coord"},
        "text": {"type": ["string", "null"]},
        "key": {"type": ["string", "null"]},
        "duration_ms": {"type": ["integer", "null"], "minimum": 0},
        "meta": {"type": ["object", "null"], "additionalProperties": True},
    },
    "$defs": {
        "coord": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "x_px": {"type": ["integer", "null"], "minimum": 0},
                "y_px": {"type": ["integer", "null"], "minimum": 0},
                "x_norm": {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0},
                "y_norm": {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0},
            },
        }
    },
    # Allow agent-specific extras; the normalizer keeps the core stable.
    "additionalProperties": True,
}

_MAS_ACTION_VALIDATOR = jsonschema.Draft202012Validator(MAS_ACTION_SCHEMA_V1)


def validate_mas_action(action: Mapping[str, Any]) -> None:
    """Raise jsonschema.ValidationError if the action doesn't match MAS schema."""

    _MAS_ACTION_VALIDATOR.validate(action)


def _normalize_action_type(action_type: Any) -> str:
    if not isinstance(action_type, str) or not action_type.strip():
        return "unknown"
    s = action_type.strip().lower().replace(" ", "_").replace("-", "_")
    if s in MAS_ACTION_TYPES_V3_1:
        return s
    aliases = {
        "click": "tap",
        "press": "tap",
        "touch": "tap",
        "tap": "tap",
        # Collapse unsupported gestures into the v3.1 core set.
        "double_tap": "tap",
        "long_tap": "long_press",
        "long_press": "long_press",
        "hold": "long_press",
        "scroll": "swipe",
        "swipe": "swipe",
        "fling": "swipe",
        "drag": "swipe",
        "input_text": "type",
        "type_text": "type",
        "enter_text": "type",
        "write": "type",
        "enter": "type",
        "keyboard_enter": "type",
        "navigate_back": "press_back",
        "press_back": "press_back",
        "back": "press_back",
        "navigate_home": "home",
        "press_home": "home",
        "home": "home",
        "wait": "wait",
        "noop": "wait",
        "no_op": "wait",
        "sleep": "wait",
        "open_app": "open_app",
        "launch_app": "open_app",
        "start_app": "open_app",
        "open_application": "open_app",
        "open_url": "open_url",
        "open_link": "open_url",
        "open_web": "open_url",
        "navigate_url": "open_url",
        "stop": "finished",
        "terminate": "finished",
        "done": "finished",
        "finish": "finished",
        "finished": "finished",
    }
    out = aliases.get(s, "unknown")
    return out if out in MAS_ACTION_TYPES_V3_1 else "unknown"


def _coord_obj(
    *,
    x_px: Optional[int],
    y_px: Optional[int],
    x_norm: Optional[float],
    y_norm: Optional[float],
) -> Dict[str, Any]:
    return {
        "x_px": x_px,
        "y_px": y_px,
        "x_norm": x_norm,
        "y_norm": y_norm,
    }


def _normalize_coord_space(v: Any) -> Optional[str]:
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "physical": "physical_px",
        "physicalpx": "physical_px",
        "screen_px": "screenshot_px",
        "screenpx": "screenshot_px",
        "screenshot": "screenshot_px",
        "logical": "logical_px",
        "logicalpx": "logical_px",
        "normalized_screen": "normalized_screenshot",
        "normalized_screenshot_px": "normalized_screenshot",
        "normalized_logical_px": "normalized_logical",
        "normalized_physical_px": "normalized_physical",
        # Shorthands commonly seen in agent outputs.
        "norm": "normalized_screenshot",
        "px": "screenshot_px",
    }
    out = aliases.get(s, s)
    supported = {
        "physical_px",
        "screenshot_px",
        "logical_px",
        "normalized_physical",
        "normalized_screenshot",
        "normalized_logical",
        "unknown",
    }
    return out if out in supported else None


def _screen_size_px_from_any(v: Any) -> Optional[ScreenSize]:
    if not isinstance(v, Mapping):
        return None
    w = _safe_int(v.get("w") if "w" in v else v.get("width_px", v.get("width")))
    h = _safe_int(v.get("h") if "h" in v else v.get("height_px", v.get("height")))
    if w is None or h is None or w <= 0 or h <= 0:
        return None
    return ScreenSize(width_px=int(w), height_px=int(h))


def _frame_boundary_from_screen(screen: Mapping[str, Any]) -> Optional[FrameBoundary]:
    boundary = FrameBoundary.from_any(screen.get("physical_frame_boundary_px"))
    if boundary is not None:
        return boundary
    screen_size = ScreenSize.from_any(screen) or _screen_size_px_from_any(
        screen.get("screenshot_size_px")
    )
    if screen_size is None:
        return None
    return FrameBoundary(left=0, top=0, right=screen_size.width_px, bottom=screen_size.height_px)


def _infer_coord_space(
    *,
    x: float,
    y: float,
    screen: Mapping[str, Any] | None,
) -> str:
    # Conservative defaults: if it looks normalized, treat as normalized screenshot coords;
    # otherwise assume screenshot pixel coords.
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and screen is not None:
        return "normalized_screenshot"
    return "screenshot_px"


def _normalize_rotation_degrees(v: Any) -> Optional[int]:
    """Normalize rotation into degrees (0/90/180/270)."""

    rot = _safe_int(v)
    if rot is None:
        return None
    if rot in (0, 90, 180, 270):
        return int(rot)
    # Android surface_orientation commonly uses 0/1/2/3 indices.
    idx_to_deg = {0: 0, 1: 90, 2: 180, 3: 270}
    if rot in idx_to_deg:
        return idx_to_deg[int(rot)]
    return None


def _rotate_norm(nx: float, ny: float, degrees_cw: int) -> Tuple[float, float]:
    r = int(degrees_cw) % 360
    if r == 90:
        return ny, 1 - nx
    if r == 180:
        return 1 - nx, 1 - ny
    if r == 270:
        return 1 - ny, nx
    return nx, ny


def _scale_uniformity(
    *, render_w: int, render_h: int, target_w: int, target_h: int
) -> Optional[float]:
    if target_w <= 0 or target_h <= 0:
        return None
    return abs((float(render_w) / float(target_w)) - (float(render_h) / float(target_h)))


def _valid_rect_xywh_from_any(v: Any) -> Optional[Tuple[int, int, int, int]]:
    """Parse (vx,vy,vw,vh) from common shapes (xywh or ltrb dicts)."""

    if isinstance(v, Mapping):
        if all(k in v for k in ("vx", "vy", "vw", "vh")):
            vx = _safe_int(v.get("vx"))
            vy = _safe_int(v.get("vy"))
            vw = _safe_int(v.get("vw"))
            vh = _safe_int(v.get("vh"))
            if None in (vx, vy, vw, vh):
                return None
            return int(vx), int(vy), int(vw), int(vh)

        if all(k in v for k in ("x", "y", "w", "h")):
            vx = _safe_int(v.get("x"))
            vy = _safe_int(v.get("y"))
            vw = _safe_int(v.get("w"))
            vh = _safe_int(v.get("h"))
            if None in (vx, vy, vw, vh):
                return None
            return int(vx), int(vy), int(vw), int(vh)

        if all(k in v for k in ("left", "top", "right", "bottom")):
            left = _safe_int(v.get("left"))
            top = _safe_int(v.get("top"))
            right = _safe_int(v.get("right"))
            bottom = _safe_int(v.get("bottom"))
            if None in (left, top, right, bottom):
                return None
            if int(right) <= int(left) or int(bottom) <= int(top):
                return None
            return int(left), int(top), int(right) - int(left), int(bottom) - int(top)

    if isinstance(v, (list, tuple)) and len(v) == 4:
        a, b, c, d = (_safe_int(x) for x in v)
        if None in (a, b, c, d):
            return None
        # Default to xywh (matches action_executor contract).
        return int(a), int(b), int(c), int(d)

    return None


def _extract_screenshot_mapping_overrides(
    action_meta: Mapping[str, Any] | None,
) -> Tuple[Optional[Tuple[int, int, int, int]], Optional[int]]:
    if action_meta is None:
        return None, None

    rect = None
    for key in (
        "valid_rect",
        "validRect",
        "render_valid_rect",
        "renderValidRect",
        "screenshot_valid_rect",
        "screenshotValidRect",
    ):
        rect = _valid_rect_xywh_from_any(action_meta.get(key))
        if rect is not None:
            break
    if rect is None:
        coord_obj = action_meta.get("coord")
        if isinstance(coord_obj, Mapping):
            rect = _valid_rect_xywh_from_any(
                coord_obj.get("valid_rect")
                or coord_obj.get("validRect")
                or coord_obj.get("render_valid_rect")
                or coord_obj.get("renderValidRect")
            )

    rot = None
    for key in (
        "rotation",
        "rotation_deg",
        "rotation_degrees",
        "render_rotation",
        "renderRotation",
        "screenshot_rotation",
        "screenshotRotation",
    ):
        rot = _normalize_rotation_degrees(action_meta.get(key))
        if rot is not None:
            break
    if rot is None:
        coord_obj = action_meta.get("coord")
        if isinstance(coord_obj, Mapping):
            rot = _normalize_rotation_degrees(
                coord_obj.get("rotation")
                or coord_obj.get("rotation_deg")
                or coord_obj.get("rotation_degrees")
                or coord_obj.get("render_rotation")
                or coord_obj.get("renderRotation")
            )

    return rect, rot


def _infer_screenshot_space_mapper(
    *,
    screen: Mapping[str, Any],
    render_size: ScreenSize,
    frame: FrameBoundary,
    warnings: List[str],
    action_meta: Mapping[str, Any] | None,
) -> Tuple[CoordinateMapper, Dict[str, Any]]:
    """Infer a render->window mapper based on screen geometry.

    The normalizer targets *window* coordinates of size (frame_w, frame_h),
    then offsets into physical coords via (frame.left, frame.top).
    """

    valid_rect_override, rotation_override = _extract_screenshot_mapping_overrides(action_meta)

    frame_w = frame.width_px
    frame_h = frame.height_px
    if frame_w <= 0 or frame_h <= 0:
        warnings.append("physical_frame_boundary_invalid")
        return (
            CoordinateMapper(
                render_w=render_size.width_px,
                render_h=render_size.height_px,
                device_w=max(1, frame_w),
                device_h=max(1, frame_h),
            ),
            {"inferred": True, "error": "invalid_frame_boundary"},
        )

    if valid_rect_override is not None and rotation_override is not None:
        mapper = CoordinateMapper(
            render_w=render_size.width_px,
            render_h=render_size.height_px,
            device_w=frame_w,
            device_h=frame_h,
            valid_rect=valid_rect_override,
            rotation=int(rotation_override),
        )
        return mapper, {
            "inferred": False,
            "valid_rect": valid_rect_override,
            "rotation": int(rotation_override),
        }

    display_size = ScreenSize.from_any(screen) or _screen_size_px_from_any(
        screen.get("logical_screen_size_px")
    )
    surface_deg = _normalize_rotation_degrees(screen.get("surface_orientation"))

    # Infer whether the render is rotated relative to the physical screen size.
    # This is intentionally heuristic: we only auto-handle 90/270 (swapped axes).
    physical_to_render_deg = 0
    if (
        display_size is not None
        and surface_deg in (90, 270)
        and render_size.width_px > 0
        and render_size.height_px > 0
    ):
        diff_unrot = _scale_uniformity(
            render_w=render_size.width_px,
            render_h=render_size.height_px,
            target_w=display_size.width_px,
            target_h=display_size.height_px,
        )
        diff_rot = _scale_uniformity(
            render_w=render_size.width_px,
            render_h=render_size.height_px,
            target_w=display_size.height_px,
            target_h=display_size.width_px,
        )
        if diff_unrot is not None and diff_rot is not None and diff_rot < diff_unrot:
            physical_to_render_deg = int(surface_deg)

    rotation = (360 - physical_to_render_deg) % 360
    if rotation_override is not None:
        rotation = int(rotation_override) % 360

    # Infer valid_rect: either render already matches the window frame, or render is a full-screen
    # screenshot and we need to project the physical_frame_boundary into render coords.
    valid_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
    inferred_valid_rect = True

    if valid_rect_override is not None:
        valid_rect = valid_rect_override
        inferred_valid_rect = False
    elif display_size is None:
        # Without full screen size, we cannot reliably map the frame boundary into render coords.
        valid_rect = (0, 0, 0, 0)
    else:
        # Compare whether render seems to match the frame or the full display (after accounting for
        # rotation).
        if physical_to_render_deg in (90, 270):
            expected_frame_w, expected_frame_h = frame_h, frame_w
            expected_disp_w, expected_disp_h = display_size.height_px, display_size.width_px
        else:
            expected_frame_w, expected_frame_h = frame_w, frame_h
            expected_disp_w, expected_disp_h = display_size.width_px, display_size.height_px

        diff_frame = _scale_uniformity(
            render_w=render_size.width_px,
            render_h=render_size.height_px,
            target_w=expected_frame_w,
            target_h=expected_frame_h,
        )
        diff_disp = _scale_uniformity(
            render_w=render_size.width_px,
            render_h=render_size.height_px,
            target_w=expected_disp_w,
            target_h=expected_disp_h,
        )

        render_is_frame = (
            diff_frame is not None and diff_disp is not None and diff_frame <= diff_disp
        )

        if render_is_frame:
            valid_rect = (0, 0, 0, 0)
        else:
            # Map the physical frame boundary into render coords (xywh in render pixels).
            #
            # IMPORTANT: Prefer a "letterbox" model (uniform scale + centered padding)
            # over per-axis scaling. Many UI agent pipelines resize screenshots into a
            # fixed render canvas with padding, rather than stretching.
            #
            # We compute the render-space content region for the *full display* first,
            # then project the physical_frame_boundary into that region.

            # 1) Determine the source size in the render orientation.
            src_w = int(display_size.width_px)
            src_h = int(display_size.height_px)
            if physical_to_render_deg in (90, 270):
                src_w, src_h = src_h, src_w

            # 2) Uniform scale (letterbox) from source -> render.
            scale = min(
                float(render_size.width_px) / float(src_w),
                float(render_size.height_px) / float(src_h),
            )
            content_w = float(src_w) * scale
            content_h = float(src_h) * scale
            pad_x = (float(render_size.width_px) - content_w) / 2.0
            pad_y = (float(render_size.height_px) - content_h) / 2.0

            # 3) Project the physical frame boundary corners into render.
            corners = (
                (float(frame.left), float(frame.top)),
                (float(frame.right), float(frame.top)),
                (float(frame.right), float(frame.bottom)),
                (float(frame.left), float(frame.bottom)),
            )
            xs: List[float] = []
            ys: List[float] = []
            for px, py in corners:
                nx = px / float(display_size.width_px)
                ny = py / float(display_size.height_px)
                nx_r, ny_r = _rotate_norm(nx, ny, physical_to_render_deg)
                x_src = nx_r * float(src_w)
                y_src = ny_r * float(src_h)
                xs.append(pad_x + x_src * scale)
                ys.append(pad_y + y_src * scale)

            vx = int(round(min(xs)))
            vy = int(round(min(ys)))
            vw = int(round(max(xs))) - vx
            vh = int(round(max(ys))) - vy
            valid_rect = (vx, vy, vw, vh)

            # Guard against degenerate/inverted rects.
            vx, vy, vw, vh = valid_rect
            if vw <= 0 or vh <= 0:
                warnings.append("invalid_inferred_valid_rect")
                valid_rect = (0, 0, 0, 0)

    mapper = CoordinateMapper(
        render_w=render_size.width_px,
        render_h=render_size.height_px,
        device_w=frame_w,
        device_h=frame_h,
        valid_rect=valid_rect,
        rotation=rotation,
    )
    return (
        mapper,
        {
            "inferred": bool(inferred_valid_rect or rotation_override is None),
            "valid_rect": tuple(int(v) for v in valid_rect),
            "rotation": int(rotation),
            "physical_to_render_rotation": int(physical_to_render_deg),
            "surface_orientation_deg": int(surface_deg) if surface_deg is not None else None,
            "display_size": (
                (int(display_size.width_px), int(display_size.height_px))
                if display_size is not None
                else None
            ),
        },
    )


def _log_coord_trace(
    *,
    log_fn: Callable[[str], None] | None,
    action_type: str,
    label: str,
    coord_space: str,
    input_xy: Tuple[float, float],
    render_xy: Tuple[float, float],
    window_xy: Tuple[int, int],
    physical_xy: Tuple[int, int],
    frame: FrameBoundary,
    trace: List[Dict[str, Any]],
) -> None:
    if not callable(log_fn):
        return

    def _f(v: Any) -> str:
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)

    log_fn(
        f"[COORD] {action_type}:{label} in[{coord_space}]=({input_xy[0]:.4f},{input_xy[1]:.4f}) "
        f"render=({render_xy[0]:.2f},{render_xy[1]:.2f}) "
        f"-> win=({window_xy[0]},{window_xy[1]}) "
        f"-> phys=({physical_xy[0]},{physical_xy[1]})"
    )

    if trace:
        meta = trace[0]
        log_fn(
            "        "
            + " ".join(
                [
                    f"render={meta.get('render_size')}",
                    f"window={meta.get('device_size')}",
                    f"valid_rect={meta.get('valid_rect')}",
                    f"rotation={meta.get('rotation')}",
                    f"frame=({frame.left},{frame.top},{frame.right},{frame.bottom})",
                ]
            )
        )
        calcs = [str(t.get("step")) for t in trace[1:] if t.get("step")]
        log_fn(f"        calcs({len(calcs)}): " + " -> ".join(calcs))

    for row in trace[1:]:
        step = str(row.get("step"))
        items = ", ".join(f"{k}={_f(v)}" for k, v in row.items() if k != "step" and v is not None)
        log_fn(f"        - {step}: {items}")


def _coord_space_to_physical_px(
    *,
    x: float,
    y: float,
    coord_space: str,
    screen: Mapping[str, Any] | None,
    warnings: List[str],
    screen_step: int | None,
    action_meta: Mapping[str, Any] | None = None,
    trace_coords: bool = False,
    log_fn: Callable[[str], None] | None = None,
    action_type: str | None = None,
    label: str | None = None,
) -> Tuple[Optional[int], Optional[int], Optional[Dict[str, Any]]]:
    """Convert a point in coord_space into physical_px.

    Coord-C: if coord_space is already physical_px, this must be identity
    (no scale/offset conversion). For all other coord spaces we map into the
    physical_frame_boundary_px rectangle.
    """

    if coord_space == "physical_px":
        x_px = int(round(float(x)))
        y_px = int(round(float(y)))
        # Keep schema constraints; do not clamp to screen bounds.
        if x_px < 0 or y_px < 0:
            warnings.append(f"coord_negative_px: ({x_px},{y_px})")
            x_px = max(0, x_px)
            y_px = max(0, y_px)
        return x_px, y_px, None

    if screen is None:
        warnings.append("screen_missing_for_coord_conversion")
        return None, None, None

    frame = _frame_boundary_from_screen(screen)
    if frame is None:
        warnings.append("physical_frame_boundary_missing")
        return None, None, None

    frame_w = frame.width_px
    frame_h = frame.height_px
    if frame_w <= 0 or frame_h <= 0:
        warnings.append("physical_frame_boundary_invalid")
        return None, None, None

    # screenshot_px chain: use action_executor's mapping
    #   render/screenshot_px -> (valid_rect + rotation) -> normalized -> window_px ->
    #   physical_px(offset)
    if coord_space in {"screenshot_px", "normalized_screenshot"}:
        # IMPORTANT: do NOT fallback to ScreenSize.from_any(screen) for screenshot source_size.
        render_size = _screen_size_px_from_any(screen.get("screenshot_size_px"))
        if render_size is None:
            warnings.append(f"screen_missing_for_coord_space:{coord_space}")
            return None, None, None

        mapper, mapper_meta = _infer_screenshot_space_mapper(
            screen=screen,
            render_size=render_size,
            frame=frame,
            warnings=warnings,
            action_meta=action_meta,
        )

        transform_warnings: List[str] = []
        input_xy = (float(x), float(y))
        render_x = float(x)
        render_y = float(y)

        if coord_space == "normalized_screenshot":
            x_norm = float(x)
            y_norm = float(y)
            if x_norm < 0.0 or x_norm > 1.0 or y_norm < 0.0 or y_norm > 1.0:
                transform_warnings.append(f"coord_out_of_range_norm: ({x_norm},{y_norm})")
                x_norm = _clamp(x_norm, 0.0, 1.0)
                y_norm = _clamp(y_norm, 0.0, 1.0)
            render_x = x_norm * float(render_size.width_px)
            render_y = y_norm * float(render_size.height_px)

        # Best-effort warnings on render coords (kept separate from mapper clamping).
        if render_x < 0.0 or render_y < 0.0:
            transform_warnings.append(
                f"coord_negative_px: ({int(round(render_x))},{int(round(render_y))})"
            )
        if coord_space == "screenshot_px":
            if render_x >= float(render_size.width_px) or render_y >= float(render_size.height_px):
                transform_warnings.append(
                    f"coord_out_of_bounds_px: ({int(round(render_x))},{int(round(render_y))}) "
                    f"not in [0,{int(render_size.width_px)})x[0,{int(render_size.height_px)})"
                )

        if trace_coords:
            (win_x, win_y), trace = mapper.to_device_with_trace((render_x, render_y))
        else:
            win_x, win_y = mapper.to_device((render_x, render_y))
            trace = []

        x_px = int(frame.left + int(win_x))
        y_px = int(frame.top + int(win_y))

        if trace_coords:
            trace.append(
                {
                    "step": "offset",
                    "offset_x": int(frame.left),
                    "offset_y": int(frame.top),
                    "x": int(x_px),
                    "y": int(y_px),
                }
            )

        vx, vy, vw, vh = mapper.valid_rect
        if vw <= 0 or vh <= 0:
            vx, vy, vw, vh = 0, 0, int(render_size.width_px), int(render_size.height_px)

        scale_x = frame_w / float(vw) if vw > 0 else 0.0
        scale_y = frame_h / float(vh) if vh > 0 else 0.0

        if transform_warnings:
            warnings.extend(transform_warnings)

        transform: Dict[str, Any] = {
            "from": coord_space,
            "to": "physical_px",
            "screen_trace_ref": f"screen_step_{int(screen_step)}"
            if screen_step is not None
            else None,
            "params": {
                "scale_x": float(scale_x),
                "scale_y": float(scale_y),
                "offset_x": int(frame.left),
                "offset_y": int(frame.top),
                "render_size": (int(render_size.width_px), int(render_size.height_px)),
                "valid_rect": (int(vx), int(vy), int(vw), int(vh)),
                "rotation": int(mapper.rotation % 360),
                "mapper_meta": mapper_meta,
            },
            "warnings": transform_warnings,
        }
        if trace_coords and trace:
            transform["trace"] = trace

        if trace_coords and trace and action_type and label:
            _log_coord_trace(
                log_fn=log_fn or print,
                action_type=str(action_type),
                label=str(label),
                coord_space=str(coord_space),
                input_xy=input_xy,
                render_xy=(float(render_x), float(render_y)),
                window_xy=(int(win_x), int(win_y)),
                physical_xy=(int(x_px), int(y_px)),
                frame=frame,
                trace=trace,
            )

        return x_px, y_px, transform

    source_size: Optional[ScreenSize] = None
    if coord_space in {"logical_px", "normalized_logical"}:
        source_size = _screen_size_px_from_any(
            screen.get("logical_screen_size_px")
        ) or ScreenSize.from_any(screen)
    elif coord_space == "normalized_physical":
        source_size = ScreenSize(width_px=frame_w, height_px=frame_h)

    if source_size is None:
        warnings.append(f"screen_missing_for_coord_space:{coord_space}")
        return None, None, None

    src_w = int(source_size.width_px)
    src_h = int(source_size.height_px)
    if src_w <= 0 or src_h <= 0:
        warnings.append(f"screen_invalid_for_coord_space:{coord_space}")
        return None, None, None

    transform_warnings: List[str] = []
    if coord_space.startswith("normalized_"):
        x_norm = float(x)
        y_norm = float(y)
        if x_norm < 0.0 or x_norm > 1.0 or y_norm < 0.0 or y_norm > 1.0:
            transform_warnings.append(f"coord_out_of_range_norm: ({x_norm},{y_norm})")
            x_norm = _clamp(x_norm, 0.0, 1.0)
            y_norm = _clamp(y_norm, 0.0, 1.0)
        src_x = x_norm * float(src_w)
        src_y = y_norm * float(src_h)
    else:
        src_x = float(x)
        src_y = float(y)

    if src_x < 0.0 or src_y < 0.0:
        transform_warnings.append(f"coord_negative_px: ({int(round(src_x))},{int(round(src_y))})")
        src_x = max(0.0, src_x)
        src_y = max(0.0, src_y)

    if src_x >= float(src_w) or src_y >= float(src_h):
        transform_warnings.append(
            f"coord_out_of_bounds_px: ({int(round(src_x))},{int(round(src_y))}) not in "
            f"[0,{src_w})x[0,{src_h})"
        )
        src_x = min(max(0.0, src_x), max(0.0, float(src_w) - 1.0))
        src_y = min(max(0.0, src_y), max(0.0, float(src_h) - 1.0))

    scale_x = frame_w / float(src_w)
    scale_y = frame_h / float(src_h)
    x_phys = float(frame.left) + (src_x * scale_x)
    y_phys = float(frame.top) + (src_y * scale_y)
    x_px = int(round(x_phys))
    y_px = int(round(y_phys))

    # Keep the final output inside the clickable physical frame.
    if x_px < frame.left or x_px >= frame.right or y_px < frame.top or y_px >= frame.bottom:
        transform_warnings.append(
            f"coord_out_of_bounds_physical_frame: ({x_px},{y_px}) not in "
            f"[{frame.left},{frame.right})x[{frame.top},{frame.bottom})"
        )
        x_px = min(max(frame.left, x_px), max(frame.left, frame.right - 1))
        y_px = min(max(frame.top, y_px), max(frame.top, frame.bottom - 1))

    if transform_warnings:
        warnings.extend(transform_warnings)

    return (
        x_px,
        y_px,
        {
            "from": coord_space,
            "to": "physical_px",
            "screen_trace_ref": f"screen_step_{int(screen_step)}"
            if screen_step is not None
            else None,
            "params": {
                "scale_x": float(scale_x),
                "scale_y": float(scale_y),
                "offset_x": int(frame.left),
                "offset_y": int(frame.top),
            },
            "warnings": transform_warnings,
        },
    )


def _physical_px_to_norm(
    *,
    x_px: int,
    y_px: int,
    screen: Mapping[str, Any] | None,
) -> Tuple[Optional[float], Optional[float]]:
    if screen is None:
        return None, None
    frame = _frame_boundary_from_screen(screen)
    if frame is None or frame.width_px <= 0 or frame.height_px <= 0:
        return None, None
    x_norm = (float(x_px) - float(frame.left)) / float(frame.width_px)
    y_norm = (float(y_px) - float(frame.top)) / float(frame.height_px)
    return _clamp(x_norm, 0.0, 1.0), _clamp(y_norm, 0.0, 1.0)


def _convert_point(
    *,
    x: float,
    y: float,
    space: str,
    screen: Optional[ScreenSize],
    warnings: List[str],
) -> Dict[str, Any]:
    x_px: Optional[int] = None
    y_px: Optional[int] = None
    x_norm: Optional[float] = None
    y_norm: Optional[float] = None

    if space == "norm":
        x_norm = float(x)
        y_norm = float(y)
        if x_norm < 0.0 or x_norm > 1.0 or y_norm < 0.0 or y_norm > 1.0:
            warnings.append(f"coord_out_of_range_norm: ({x_norm},{y_norm})")
            x_norm = _clamp(x_norm, 0.0, 1.0)
            y_norm = _clamp(y_norm, 0.0, 1.0)

        if screen is None:
            warnings.append("screen_missing_for_norm_to_px")
        else:
            x_px = int(round(x_norm * screen.width_px))
            y_px = int(round(y_norm * screen.height_px))

    else:  # px
        x_px = int(round(float(x)))
        y_px = int(round(float(y)))
        if x_px < 0 or y_px < 0:
            warnings.append(f"coord_negative_px: ({x_px},{y_px})")
            x_px = max(0, x_px)
            y_px = max(0, y_px)

        if screen is None:
            warnings.append("screen_missing_for_px_to_norm")
        else:
            if x_px >= screen.width_px or y_px >= screen.height_px:
                warnings.append(
                    f"coord_out_of_bounds_px: ({x_px},{y_px}) not in "
                    f"[0,{screen.width_px})x[0,{screen.height_px})"
                )
                x_px = min(max(0, x_px), max(0, screen.width_px - 1))
                y_px = min(max(0, y_px), max(0, screen.height_px - 1))
            x_norm = x_px / float(screen.width_px) if screen.width_px else None
            y_norm = y_px / float(screen.height_px) if screen.height_px else None
            if x_norm is not None:
                x_norm = _clamp(float(x_norm), 0.0, 1.0)
            if y_norm is not None:
                y_norm = _clamp(float(y_norm), 0.0, 1.0)

    if screen is not None and x_px is not None and y_px is not None:
        # Clamp px derived from norm conversion to valid pixel indices.
        if x_px >= screen.width_px:
            warnings.append(f"coord_px_clamped_x: {x_px} -> {screen.width_px - 1}")
            x_px = max(0, screen.width_px - 1)
        if y_px >= screen.height_px:
            warnings.append(f"coord_px_clamped_y: {y_px} -> {screen.height_px - 1}")
            y_px = max(0, screen.height_px - 1)

    return _coord_obj(x_px=x_px, y_px=y_px, x_norm=x_norm, y_norm=y_norm)


def _guess_space(x: float, y: float, screen: Optional[ScreenSize]) -> str:
    # If it looks like normalized coords, treat as such. Screen presence makes it safer.
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and (screen is not None):
        return "norm"
    return "px"


def _extract_point(raw_action: Mapping[str, Any]) -> Tuple[Optional[float], Optional[float], str]:
    # Explicit px fields.
    x_px = _safe_float(raw_action.get("x_px"))
    y_px = _safe_float(raw_action.get("y_px"))
    if x_px is not None and y_px is not None:
        return x_px, y_px, "px"

    # Common nested object: {"coord": {"x": .., "y": ..}}.
    coord_obj = raw_action.get("coord")
    if isinstance(coord_obj, Mapping):
        x_obj = _safe_float(coord_obj.get("x_px")) if "x_px" in coord_obj else None
        y_obj = _safe_float(coord_obj.get("y_px")) if "y_px" in coord_obj else None
        if x_obj is None or y_obj is None:
            x_obj = _safe_float(coord_obj.get("x"))
            y_obj = _safe_float(coord_obj.get("y"))
        if x_obj is not None and y_obj is not None:
            return x_obj, y_obj, "auto"

    # Common x/y fields.
    x = _safe_float(raw_action.get("x"))
    y = _safe_float(raw_action.get("y"))
    if x is not None and y is not None:
        return x, y, "auto"

    # Common tuple/list.
    coord = raw_action.get("coordinate") or raw_action.get("coord")
    if isinstance(coord, (list, tuple)):
        if len(coord) == 2:
            x2, y2 = _safe_float(coord[0]), _safe_float(coord[1])
            if x2 is not None and y2 is not None:
                return x2, y2, "auto"
        if len(coord) == 4:
            x1, y1, x2, y2 = (_safe_float(v) for v in coord)
            if None not in (x1, y1, x2, y2):
                return (x1 + x2) / 2.0, (y1 + y2) / 2.0, "auto"

    bbox = raw_action.get("bbox") or raw_action.get("bounds")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        x1, y1, x2, y2 = (_safe_float(v) for v in bbox)
        if None not in (x1, y1, x2, y2):
            return (x1 + x2) / 2.0, (y1 + y2) / 2.0, "auto"

    return None, None, "auto"


def _extract_swipe_points(
    raw_action: Mapping[str, Any],
) -> SwipePoints:
    start_obj = raw_action.get("start")
    end_obj = raw_action.get("end")
    if isinstance(start_obj, Mapping) and isinstance(end_obj, Mapping):
        sx_obj = _safe_float(start_obj.get("x_px")) if "x_px" in start_obj else None
        sy_obj = _safe_float(start_obj.get("y_px")) if "y_px" in start_obj else None
        ex_obj = _safe_float(end_obj.get("x_px")) if "x_px" in end_obj else None
        ey_obj = _safe_float(end_obj.get("y_px")) if "y_px" in end_obj else None
        if None in (sx_obj, sy_obj, ex_obj, ey_obj):
            sx_obj = _safe_float(start_obj.get("x"))
            sy_obj = _safe_float(start_obj.get("y"))
            ex_obj = _safe_float(end_obj.get("x"))
            ey_obj = _safe_float(end_obj.get("y"))
        if None not in (sx_obj, sy_obj, ex_obj, ey_obj):
            return (sx_obj, sy_obj, "auto"), (ex_obj, ey_obj, "auto")

    # Explicit px fields.
    sx = _safe_float(raw_action.get("start_x_px")) or _safe_float(raw_action.get("start_x"))
    sy = _safe_float(raw_action.get("start_y_px")) or _safe_float(raw_action.get("start_y"))
    ex = _safe_float(raw_action.get("end_x_px")) or _safe_float(raw_action.get("end_x"))
    ey = _safe_float(raw_action.get("end_y_px")) or _safe_float(raw_action.get("end_y"))
    if None not in (sx, sy, ex, ey):
        return (sx, sy, "auto"), (ex, ey, "auto")

    c1 = raw_action.get("coordinate") or raw_action.get("start") or raw_action.get("coord")
    c2 = raw_action.get("coordinate2") or raw_action.get("end") or raw_action.get("coord2")
    if isinstance(c1, (list, tuple)) and isinstance(c2, (list, tuple)):
        if len(c1) == 2 and len(c2) == 2:
            sx2, sy2 = _safe_float(c1[0]), _safe_float(c1[1])
            ex2, ey2 = _safe_float(c2[0]), _safe_float(c2[1])
            return (sx2, sy2, "auto"), (ex2, ey2, "auto")
        if len(c1) == 4 and len(c2) == 4:
            x1, y1, x2, y2 = (_safe_float(v) for v in c1)
            x3, y3, x4, y4 = (_safe_float(v) for v in c2)
            if None not in (x1, y1, x2, y2, x3, y3, x4, y4):
                return ((x1 + x2) / 2.0, (y1 + y2) / 2.0, "auto"), (
                    (x3 + x4) / 2.0,
                    (y3 + y4) / 2.0,
                    "auto",
                )

    return (None, None, "auto"), (None, None, "auto")


def _has_element_index(raw_action: Mapping[str, Any]) -> bool:
    for key in (
        "element_index",
        "element_idx",
        "element_id",
        "elementId",
        "target_index",
        "list_index",
        "item_index",
        "index",
        "idx",
    ):
        if raw_action.get(key) is not None:
            return True
    return False


def normalize_action(
    raw_action: Mapping[str, Any],
    *,
    screen: Mapping[str, Any] | None = None,
    screen_step: int | None = None,
    ref_obs_digest: Optional[str] = None,
    ref_check_applicable: Optional[bool] = None,
    trace_coords: bool | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> Tuple[ActionDict, List[str]]:
    """Normalize an agent action to MAS schema.

    Returns:
      (normalized_action, warnings)
    """

    warnings: List[str] = []
    raw_action = dict(raw_action) if isinstance(raw_action, Mapping) else {"raw": raw_action}

    trace_coords_flag = bool(trace_coords) if trace_coords is not None else False
    if trace_coords is None:
        raw_trace = (
            raw_action.get("trace_coords")
            if "trace_coords" in raw_action
            else raw_action.get("traceCoords")
        )
        if isinstance(raw_trace, bool):
            trace_coords_flag = raw_trace

    raw_ref_obs_digest = raw_action.get("ref_obs_digest")
    if (
        ref_obs_digest is None
        and isinstance(raw_ref_obs_digest, str)
        and raw_ref_obs_digest.strip()
    ):
        ref_obs_digest = raw_ref_obs_digest
    raw_ref_check_applicable = raw_action.get("ref_check_applicable")
    if ref_check_applicable is None and isinstance(raw_ref_check_applicable, bool):
        ref_check_applicable = raw_ref_check_applicable

    raw_action_type = (
        raw_action.get("type")
        if "type" in raw_action
        else raw_action.get("action_type", raw_action.get("action"))
    )
    raw_action_type_norm: Optional[str] = None
    if isinstance(raw_action_type, str) and raw_action_type.strip():
        raw_action_type_norm = raw_action_type.strip().lower().replace(" ", "_").replace("-", "_")

    action_type = _normalize_action_type(raw_action_type)

    normalized: MutableMapping[str, Any] = {"type": action_type, "meta": None}
    has_element_index = _has_element_index(raw_action)

    if action_type in {"tap", "long_press"}:
        x, y, _ = _extract_point(raw_action)
        if x is None or y is None:
            warnings.append("missing_coord")
            normalized["coord_space"] = "physical_px"
            normalized["coord"] = _coord_obj(x_px=None, y_px=None, x_norm=None, y_norm=None)
        else:
            coord_space = _normalize_coord_space(
                raw_action.get("coord_space") or raw_action.get("coordSpace")
            )
            coord_obj = raw_action.get("coord")
            if coord_space is None and isinstance(coord_obj, Mapping):
                coord_space = _normalize_coord_space(
                    coord_obj.get("coord_space") or coord_obj.get("coordSpace")
                )
            if coord_space is None:
                coord_space = _infer_coord_space(x=float(x), y=float(y), screen=screen)

            x_px, y_px, transform = _coord_space_to_physical_px(
                x=float(x),
                y=float(y),
                coord_space=coord_space,
                screen=screen,
                warnings=warnings,
                screen_step=screen_step,
                action_meta=raw_action,
                trace_coords=trace_coords_flag,
                log_fn=log_fn,
                action_type=action_type,
                label="coord",
            )
            normalized["coord_space"] = "physical_px"
            if x_px is None or y_px is None:
                normalized["coord"] = _coord_obj(x_px=None, y_px=None, x_norm=None, y_norm=None)
            else:
                x_norm, y_norm = _physical_px_to_norm(x_px=x_px, y_px=y_px, screen=screen)
                normalized["coord"] = _coord_obj(x_px=x_px, y_px=y_px, x_norm=x_norm, y_norm=y_norm)
            if transform is not None:
                normalized["coord_transform"] = transform

    elif action_type == "swipe":
        (sx, sy, _), (ex, ey, _) = _extract_swipe_points(raw_action)
        if None in (sx, sy, ex, ey):
            warnings.append("missing_swipe_coords")
            normalized["coord_space"] = "physical_px"
            normalized["start"] = _coord_obj(x_px=None, y_px=None, x_norm=None, y_norm=None)
            normalized["end"] = _coord_obj(x_px=None, y_px=None, x_norm=None, y_norm=None)
        else:
            coord_space = _normalize_coord_space(
                raw_action.get("coord_space") or raw_action.get("coordSpace")
            )
            if coord_space is None:
                # Use the start point to infer a default.
                coord_space = _infer_coord_space(x=float(sx), y=float(sy), screen=screen)

            sx_px, sy_px, t1 = _coord_space_to_physical_px(
                x=float(sx),
                y=float(sy),
                coord_space=coord_space,
                screen=screen,
                warnings=warnings,
                screen_step=screen_step,
                action_meta=raw_action,
                trace_coords=trace_coords_flag,
                log_fn=log_fn,
                action_type=action_type,
                label="start",
            )
            ex_px, ey_px, t2 = _coord_space_to_physical_px(
                x=float(ex),
                y=float(ey),
                coord_space=coord_space,
                screen=screen,
                warnings=warnings,
                screen_step=screen_step,
                action_meta=raw_action,
                trace_coords=trace_coords_flag,
                log_fn=log_fn,
                action_type=action_type,
                label="end",
            )

            normalized["coord_space"] = "physical_px"
            if None in (sx_px, sy_px, ex_px, ey_px):
                normalized["start"] = _coord_obj(x_px=None, y_px=None, x_norm=None, y_norm=None)
                normalized["end"] = _coord_obj(x_px=None, y_px=None, x_norm=None, y_norm=None)
            else:
                sx_norm, sy_norm = _physical_px_to_norm(
                    x_px=int(sx_px), y_px=int(sy_px), screen=screen
                )
                ex_norm, ey_norm = _physical_px_to_norm(
                    x_px=int(ex_px), y_px=int(ey_px), screen=screen
                )
                normalized["start"] = _coord_obj(
                    x_px=int(sx_px),
                    y_px=int(sy_px),
                    x_norm=sx_norm,
                    y_norm=sy_norm,
                )
                normalized["end"] = _coord_obj(
                    x_px=int(ex_px),
                    y_px=int(ey_px),
                    x_norm=ex_norm,
                    y_norm=ey_norm,
                )

            coord_transform = t1 or t2
            if t1 is not None and t2 is not None:
                merged = []
                for w in (t1.get("warnings") or []) + (t2.get("warnings") or []):
                    if w not in merged:
                        merged.append(w)
                coord_transform = dict(t1)
                coord_transform["warnings"] = merged
                trace_start = t1.get("trace")
                trace_end = t2.get("trace")
                if trace_start is not None or trace_end is not None:
                    coord_transform.pop("trace", None)
                    coord_transform["trace"] = {"start": trace_start, "end": trace_end}
            if coord_transform is not None:
                normalized["coord_transform"] = coord_transform

            duration_ms = _safe_int(raw_action.get("duration_ms"))
            if duration_ms is not None:
                normalized["duration_ms"] = duration_ms

    elif action_type == "type":
        text = (
            raw_action.get("text")
            if "text" in raw_action
            else raw_action.get("value", raw_action.get("input", raw_action.get("content")))
        )
        key = raw_action.get("key")
        if key is None and raw_action_type_norm in {"enter", "keyboard_enter"}:
            key = "enter"

        if key is not None and not isinstance(key, str):
            key = str(key)
        if key is not None and str(key).strip():
            normalized["key"] = str(key)

        if isinstance(text, str):
            normalized["text"] = text
        elif text is not None:
            normalized["text"] = str(text)
        elif "key" not in normalized:
            warnings.append("missing_text")
            normalized["text"] = ""

    elif action_type == "open_app":
        package = (
            raw_action.get("package")
            or raw_action.get("package_name")
            or raw_action.get("app_package")
            or raw_action.get("app_id")
            or raw_action.get("app")
            or raw_action.get("app_name")
        )
        if package is None or package == "":
            warnings.append("missing_package")
            normalized["package"] = None
        else:
            normalized["package"] = package if isinstance(package, str) else str(package)

    elif action_type == "open_url":
        url = raw_action.get("url") or raw_action.get("uri") or raw_action.get("link")
        if url is None or url == "":
            warnings.append("missing_url")
            normalized["url"] = None
        else:
            normalized["url"] = url if isinstance(url, str) else str(url)

    elif action_type == "wait":
        duration_ms = _safe_int(raw_action.get("duration_ms") or raw_action.get("ms"))
        if duration_ms is None:
            seconds = _safe_float(raw_action.get("seconds") or raw_action.get("duration_s"))
            if seconds is not None and seconds >= 0:
                duration_ms = int(round(float(seconds) * 1000.0))
        if duration_ms is not None:
            normalized["duration_ms"] = duration_ms

    elif action_type in {"press_back", "home", "finished"}:
        pass

    else:  # unknown
        # Unknown action types are still schema-valid; capture raw type for debugging.
        if raw_action_type_norm is None:
            warnings.append("missing_action_type")
        else:
            warnings.append(f"unknown_action_type:{raw_action_type_norm}")
        normalized["meta"] = {"raw_action_type": raw_action_type}

    needs_ref = action_type in {"tap", "long_press", "swipe"} or has_element_index
    if needs_ref:
        if ref_check_applicable is None:
            ref_check_applicable = ref_obs_digest is not None
        if ref_check_applicable:
            normalized["ref_check_applicable"] = True
            if isinstance(ref_obs_digest, str) and ref_obs_digest.strip():
                normalized["ref_obs_digest"] = ref_obs_digest
            else:
                warnings.append("missing_ref_obs_digest")
        else:
            normalized["ref_check_applicable"] = False
            # Keep the key present for schema stability and Phase-3 audit_only downgrade.
            normalized["ref_obs_digest"] = None

    # Ensure schema-valid (tests rely on this).
    validate_mas_action(normalized)
    return dict(normalized), warnings
