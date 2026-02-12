from __future__ import annotations

import pytest

from mas_harness.evidence.action_normalizer import normalize_action, validate_mas_action


def test_action_normalizer_tap_norm_to_px() -> None:
    action, warnings = normalize_action(
        {"type": "tap", "x": 0.5, "y": 0.25},
        screen={"width_px": 1000, "height_px": 2000, "screenshot_size_px": {"w": 1000, "h": 2000}},
    )
    validate_mas_action(action)
    assert action["type"] == "tap"
    assert action["coord_space"] == "physical_px"
    assert action["coord"]["x_px"] == 500
    assert action["coord"]["y_px"] == 500
    assert warnings == []


def test_action_normalizer_tap_px_to_norm_and_clamp() -> None:
    action, warnings = normalize_action(
        {"type": "tap", "x": 1200, "y": 1999},
        screen={"width_px": 1000, "height_px": 2000, "screenshot_size_px": {"w": 1000, "h": 2000}},
    )
    validate_mas_action(action)
    assert action["type"] == "tap"
    assert action["coord_space"] == "physical_px"
    assert action["coord"]["x_px"] == 999
    assert action["coord"]["y_px"] == 1999
    assert any(w.startswith("coord_out_of_bounds_px") for w in warnings)


def test_action_normalizer_swipe_coords() -> None:
    action, warnings = normalize_action(
        {
            "type": "swipe",
            "coordinate": [0.1, 0.2],
            "coordinate2": [0.9, 0.8],
        },
        screen={"width_px": 1000, "height_px": 2000, "screenshot_size_px": {"w": 1000, "h": 2000}},
    )
    validate_mas_action(action)
    assert action["type"] == "swipe"
    assert action["coord_space"] == "physical_px"
    assert action["start"]["x_px"] == 100
    assert action["start"]["y_px"] == 400
    assert action["end"]["x_px"] == 900
    assert action["end"]["y_px"] == 1600
    assert warnings == []


def test_action_normalizer_missing_params_is_schema_valid() -> None:
    action, warnings = normalize_action({"type": "tap"}, screen={"width_px": 100, "height_px": 200})
    validate_mas_action(action)
    assert action["type"] == "tap"
    assert action["coord_space"] == "physical_px"
    assert action["coord"]["x_px"] is None
    assert "missing_coord" in warnings


def test_action_normalizer_action_set_v3_1_aliases() -> None:
    action, warnings = normalize_action({"type": "type_text", "text": "hi"})
    validate_mas_action(action)
    assert action["type"] == "type"
    assert action["text"] == "hi"
    assert warnings == []

    action, warnings = normalize_action({"type": "back"})
    validate_mas_action(action)
    assert action["type"] == "press_back"
    assert warnings == []

    action, warnings = normalize_action({"type": "stop"})
    validate_mas_action(action)
    assert action["type"] == "finished"
    assert warnings == []


def test_action_normalizer_action_set_v3_1_open_app_url() -> None:
    action, warnings = normalize_action({"type": "open_app", "package": "com.example.app"})
    validate_mas_action(action)
    assert action["type"] == "open_app"
    assert action["package"] == "com.example.app"
    assert warnings == []

    action, warnings = normalize_action({"type": "open_url", "url": "https://example.com"})
    validate_mas_action(action)
    assert action["type"] == "open_url"
    assert action["url"] == "https://example.com"
    assert warnings == []


def test_action_normalizer_coord_space_physical_px_identity() -> None:
    screen = {
        "width_px": 1000,
        "height_px": 2000,
        "screenshot_size_px": {"w": 500, "h": 900},
        "logical_screen_size_px": {"w": 2000, "h": 3600},
        "physical_frame_boundary_px": {"left": 0, "top": 100, "right": 1000, "bottom": 1900},
    }
    action, warnings = normalize_action(
        {"type": "tap", "coord_space": "physical_px", "coord": {"x": 500, "y": 1000}},
        screen=screen,
        screen_step=12,
    )
    validate_mas_action(action)
    assert action["type"] == "tap"
    assert action["coord_space"] == "physical_px"
    assert action["coord"]["x_px"] == 500
    assert action["coord"]["y_px"] == 1000
    assert "coord_transform" not in action
    assert warnings == []


def test_action_normalizer_coord_space_physical_px_identity_without_screen() -> None:
    action, warnings = normalize_action(
        {"type": "tap", "coord_space": "physical_px", "coord": {"x": 123, "y": 456}},
        screen=None,
        screen_step=12,
    )
    validate_mas_action(action)
    assert action["type"] == "tap"
    assert action["coord_space"] == "physical_px"
    assert action["coord"]["x_px"] == 123
    assert action["coord"]["y_px"] == 456
    assert action["coord"]["x_norm"] is None
    assert action["coord"]["y_norm"] is None
    assert "coord_transform" not in action
    assert warnings == []


@pytest.mark.parametrize(
    ("coord_space", "coord_x", "coord_y", "scale_x", "scale_y"),
    [
        ("screenshot_px", 250, 450, 2.0, 2.0),
        ("normalized_screenshot", 0.5, 0.5, 2.0, 2.0),
        ("logical_px", 1000, 1800, 0.5, 0.5),
        ("normalized_logical", 0.5, 0.5, 0.5, 0.5),
        ("normalized_physical", 0.5, 0.5, 1.0, 1.0),
    ],
)
def test_action_normalizer_coord_space_to_physical_px(
    coord_space: str,
    coord_x: float,
    coord_y: float,
    scale_x: float,
    scale_y: float,
) -> None:
    screen = {
        "width_px": 1000,
        "height_px": 2000,
        "screenshot_size_px": {"w": 500, "h": 900},
        "logical_screen_size_px": {"w": 2000, "h": 3600},
        "physical_frame_boundary_px": {"left": 0, "top": 100, "right": 1000, "bottom": 1900},
    }
    action, warnings = normalize_action(
        {"type": "tap", "coord_space": coord_space, "coord": {"x": coord_x, "y": coord_y}},
        screen=screen,
        screen_step=12,
    )
    validate_mas_action(action)
    assert action["type"] == "tap"
    assert action["coord_space"] == "physical_px"
    assert action["coord"]["x_px"] == 500
    assert action["coord"]["y_px"] == 1000
    transform = action["coord_transform"]
    assert transform["from"] == coord_space
    assert transform["to"] == "physical_px"
    assert transform["screen_trace_ref"] == "screen_step_12"
    assert transform["params"]["scale_x"] == scale_x
    assert transform["params"]["scale_y"] == scale_y
    assert transform["params"]["offset_x"] == 0
    assert transform["params"]["offset_y"] == 100
    assert warnings == []


def test_action_normalizer_swipe_coord_space_screenshot_px() -> None:
    screen = {
        "width_px": 1000,
        "height_px": 2000,
        "screenshot_size_px": {"w": 500, "h": 900},
        "logical_screen_size_px": {"w": 2000, "h": 3600},
        "physical_frame_boundary_px": {"left": 0, "top": 100, "right": 1000, "bottom": 1900},
    }
    action, warnings = normalize_action(
        {
            "type": "swipe",
            "coord_space": "screenshot_px",
            "start": {"x": 10, "y": 20},
            "end": {"x": 490, "y": 880},
            "duration_ms": 123,
        },
        screen=screen,
        screen_step=7,
    )
    validate_mas_action(action)
    assert action["type"] == "swipe"
    assert action["coord_space"] == "physical_px"
    assert action["start"]["x_px"] == 20
    assert action["start"]["y_px"] == 140
    assert action["end"]["x_px"] == 980
    assert action["end"]["y_px"] == 1860
    assert action["duration_ms"] == 123
    assert action["coord_transform"]["from"] == "screenshot_px"
    assert action["coord_transform"]["to"] == "physical_px"
    assert action["coord_transform"]["screen_trace_ref"] == "screen_step_7"
    assert warnings == []
