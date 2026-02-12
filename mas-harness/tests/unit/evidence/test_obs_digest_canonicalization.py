from __future__ import annotations

from pathlib import Path

from mas_harness.evidence import _TINY_PNG_1X1, EvidenceWriter


def test_obs_digest_debounced_default_ignores_notifications_order(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case_obs_debounce", seed=0)

    base = {
        "screenshot_png": _TINY_PNG_1X1,
        "screen_info": {
            "width_px": 100,
            "height_px": 200,
            "density_dpi": 320,
            "surface_orientation": 0,
            # Jittery fields should not affect the digest (not part of geometry_digest).
            "dumpsys_window": {"focusedWindow": "W1"},
        },
        "foreground": {"package": "com.example.app", "activity": "MainActivity"},
        "notifications": [
            {"pkg": "a", "title": "t1", "text": "x"},
            {"pkg": "b", "title": "t2", "text": "y"},
        ],
        "a11y_tree": {"nodes": [{"id": "root", "role": "window", "children": []}]},
        "clipboard": {"text": "hello", "op": "copy"},
    }
    writer.record_observation(step=0, observation=dict(base))
    d1 = writer.last_obs_digest

    noisy = dict(base)
    noisy["notifications"] = list(reversed(base["notifications"]))
    noisy["a11y_tree"] = {"nodes": [{"id": "root", "role": "window", "children": ["label"]}]}
    noisy["clipboard"] = {"text": "world", "op": "copy"}
    writer.record_observation(step=1, observation=noisy)
    d2 = writer.last_obs_digest

    writer.close()

    assert isinstance(d1, str) and d1
    assert isinstance(d2, str) and d2
    assert d1 == d2


def test_obs_digest_debounced_ignores_screen_dumpsys_jitter(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case_obs_dumpsys_jitter", seed=0)

    base = {
        "screenshot_png": _TINY_PNG_1X1,
        "screen_info": {
            "width_px": 100,
            "height_px": 200,
            "density_dpi": 320,
            "surface_orientation": 0,
            "dumpsys_window": {"focusedWindow": "W1"},
        },
        "foreground": {"package": "com.example.app", "activity": "MainActivity"},
        "notifications": [],
        "a11y_tree": {"nodes": [{"id": "root", "role": "window", "children": []}]},
        "clipboard": None,
    }
    writer.record_observation(step=0, observation=dict(base))
    d1 = writer.last_obs_digest

    jitter = dict(base)
    jitter["screen_info"] = dict(base["screen_info"])
    jitter["screen_info"]["dumpsys_window"] = {"focusedWindow": "W2", "ts_ms": 1234567890}
    writer.record_observation(step=1, observation=jitter)
    d2 = writer.last_obs_digest

    writer.close()

    assert isinstance(d1, str) and d1
    assert isinstance(d2, str) and d2
    assert d1 == d2


def test_obs_digest_debounced_ignores_uiautomator_node_order(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case_obs_ui_order", seed=0)

    xml_a = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="A" resource-id="id/a" class="android.widget.TextView" package="com.example"
        content-desc="" clickable="false" bounds="[0,0][10,10]" />
  <node index="1" text="B" resource-id="id/b" class="android.widget.Button" package="com.example"
        content-desc="" clickable="true" bounds="[10,0][20,10]" />
</hierarchy>
"""

    xml_b = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="5" text="B" resource-id="id/b" class="android.widget.Button" package="com.example"
        content-desc="" clickable="true" bounds="[10,0][20,10]" />
  <node index="6" text="A" resource-id="id/a" class="android.widget.TextView" package="com.example"
        content-desc="" clickable="false" bounds="[0,0][10,10]" />
</hierarchy>
"""

    base = {
        "screenshot_png": _TINY_PNG_1X1,
        "screen_info": {
            "width_px": 100,
            "height_px": 200,
            "density_dpi": 320,
            "surface_orientation": 0,
        },
        "foreground": {"package": "com.example.app", "activity": "MainActivity"},
        "uiautomator_xml": xml_a,
        "notifications": [],
        "clipboard": None,
    }
    writer.record_observation(step=0, observation=dict(base))
    d1 = writer.last_obs_digest

    swapped = dict(base)
    swapped["uiautomator_xml"] = xml_b
    writer.record_observation(step=1, observation=swapped)
    d2 = writer.last_obs_digest

    writer.close()

    assert isinstance(d1, str) and d1
    assert isinstance(d2, str) and d2
    assert d1 == d2


def test_obs_digest_changes_when_uiautomator_content_changes(tmp_path: Path) -> None:
    writer = EvidenceWriter(run_dir=tmp_path, case_id="case_obs_ui_change", seed=0)

    xml_a = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="A" resource-id="id/a" class="android.widget.TextView" package="com.example"
        content-desc="" clickable="false" bounds="[0,0][10,10]" />
  <node index="1" text="B" resource-id="id/b" class="android.widget.Button" package="com.example"
        content-desc="" clickable="true" bounds="[10,0][20,10]" />
</hierarchy>
"""

    xml_changed = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="A" resource-id="id/a" class="android.widget.TextView" package="com.example"
        content-desc="" clickable="false" bounds="[0,0][10,10]" />
  <node index="1" text="C" resource-id="id/b" class="android.widget.Button" package="com.example"
        content-desc="" clickable="true" bounds="[10,0][20,10]" />
</hierarchy>
"""

    base = {
        "screenshot_png": _TINY_PNG_1X1,
        "screen_info": {
            "width_px": 100,
            "height_px": 200,
            "density_dpi": 320,
            "surface_orientation": 0,
        },
        "foreground": {"package": "com.example.app", "activity": "MainActivity"},
        "uiautomator_xml": xml_a,
        "notifications": [],
        "clipboard": None,
    }
    writer.record_observation(step=0, observation=dict(base))
    d1 = writer.last_obs_digest

    changed = dict(base)
    changed["uiautomator_xml"] = xml_changed
    writer.record_observation(step=1, observation=changed)
    d2 = writer.last_obs_digest

    writer.close()

    assert isinstance(d1, str) and d1
    assert isinstance(d2, str) and d2
    assert d1 != d2
