from __future__ import annotations

from mas_harness.evidence.ui_elements import UiElementsExtractor


def test_uiautomator_xml_includes_state_fields() -> None:
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="Hello" resource-id="id/btn" class="android.widget.Button"
        package="com.example" content-desc="" clickable="true" enabled="false" focused="true"
        selected="false" checked="true" scrollable="false"
        bounds="[1,2][3,4]" />
</hierarchy>
"""

    ex = UiElementsExtractor(max_elements=10)
    out = ex.extract(uiautomator_xml=xml, default_package=None)
    assert out.source == "uiautomator"
    assert len(out.ui_elements) == 1
    el = out.ui_elements[0]
    assert el["bbox"] == [1, 2, 3, 4]
    assert el["resource_id"] == "id/btn"
    assert el["enabled"] is False
    assert el["focused"] is True
    assert el["selected"] is False
    assert el["checked"] is True
    assert el["scrollable"] is False


def test_a11y_tree_maps_optional_state_fields() -> None:
    a11y_tree = {
        "nodes": [
            {
                "boundsInScreen": [0, 0, 10, 10],
                "label": "A",
                "packageName": "pkg",
                "clickable": "false",
                "enabled": "true",
                "isFocused": 0,
                "isSelected": 1,
                "checked": False,
                "isScrollable": "no",
            }
        ]
    }

    ex = UiElementsExtractor(max_elements=10)
    out = ex.extract(a11y_tree=a11y_tree, uiautomator_xml=None, default_package=None)
    assert out.source == "a11y"
    assert len(out.ui_elements) == 1
    el = out.ui_elements[0]
    assert el["enabled"] is True
    assert el["focused"] is False
    assert el["selected"] is True
    assert el["checked"] is False
    assert el["scrollable"] is False


def test_synthesize_preserves_optional_state_fields() -> None:
    ex = UiElementsExtractor(max_elements=10)
    ui_elements = [
        {
            "bbox": [0, 0, 10, 10],
            "clickable": False,
            "package": "pkg",
            "text": "A",
            "desc": None,
            "resource_id": None,
            "enabled": True,
            "focused": False,
            "selected": True,
            "checked": False,
            "scrollable": False,
        }
    ]
    xml = ex.synthesize_uiautomator_xml(ui_elements=ui_elements, rotation=0)
    out = ex.extract(uiautomator_xml=xml, default_package=None)
    assert out.source == "uiautomator"
    el = out.ui_elements[0]
    assert el["enabled"] is True
    assert el["focused"] is False
    assert el["selected"] is True
    assert el["checked"] is False
    assert el["scrollable"] is False
