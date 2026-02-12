from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


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
    if isinstance(v, dict):
        maybe = [_safe_int(v.get(k)) for k in ("left", "top", "right", "bottom")]
        if None not in maybe:
            v = maybe
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    a, b, c, d = (_safe_int(x) for x in v)
    if None in (a, b, c, d):
        return None
    x1, y1, x2, y2 = int(a), int(b), int(c), int(d)
    if x2 < x1 or y2 < y1:
        return None
    return x1, y1, x2, y2


_UIAUTOMATOR_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _parse_uiautomator_bounds(bounds: Any) -> Optional[Tuple[int, int, int, int]]:
    if not isinstance(bounds, str):
        return None
    m = _UIAUTOMATOR_BOUNDS_RE.search(bounds)
    if not m:
        return None
    x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
    if x2 < x1 or y2 < y1:
        return None
    return x1, y1, x2, y2


def _clean_text(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    s = s.replace("\u0000", "").strip()
    return s or None


@dataclass(frozen=True)
class UiElementsExtraction:
    source: str
    ui_elements: List[Dict[str, Any]]
    errors: List[str]


class UiElementsExtractor:
    """Extract a normalized list of UI elements from a11y trees or UIAutomator XML.

    Elements include stable core fields (bbox/clickable/package/text/desc/resource_id) and
    optional UI state flags (enabled/focused/selected/checked/scrollable) when available.
    """

    def __init__(self, *, max_elements: int = 5000) -> None:
        self._max_elements = int(max_elements)

    def extract(
        self,
        *,
        a11y_tree: Optional[Dict[str, Any]] = None,
        uiautomator_xml: str | bytes | None = None,
        default_package: Optional[str] = None,
    ) -> UiElementsExtraction:
        if uiautomator_xml is not None:
            xml_text = (
                uiautomator_xml.decode("utf-8", errors="replace")
                if isinstance(uiautomator_xml, (bytes, bytearray))
                else str(uiautomator_xml)
            )
            extracted = self._from_uiautomator_xml(xml_text, default_package=default_package)
            if extracted.ui_elements:
                return extracted

        if isinstance(a11y_tree, dict):
            return self._from_a11y_tree(a11y_tree, default_package=default_package)

        return UiElementsExtraction(source="none", ui_elements=[], errors=["no_input"])

    def synthesize_uiautomator_xml(
        self,
        *,
        ui_elements: Sequence[Dict[str, Any]],
        rotation: int | None = None,
    ) -> str:
        rot = int(rotation) if isinstance(rotation, int) else 0
        lines: List[str] = [
            "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>",
            f'<hierarchy rotation="{rot}">',
        ]
        for idx, el in enumerate(ui_elements):
            bbox = el.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = bbox
            bounds = f"[{int(x1)},{int(y1)}][{int(x2)},{int(y2)}]"
            text = _clean_text(el.get("text")) or ""
            desc = _clean_text(el.get("desc")) or ""
            res_id = _clean_text(el.get("resource_id")) or ""
            pkg = _clean_text(el.get("package")) or ""
            clickable = "true" if el.get("clickable") is True else "false"
            enabled = _safe_bool(el.get("enabled"))
            focused = _safe_bool(el.get("focused"))
            selected = _safe_bool(el.get("selected"))
            checked = _safe_bool(el.get("checked"))
            scrollable = _safe_bool(el.get("scrollable"))

            extra_attrs = ""
            if enabled is not None:
                extra_attrs += f" enabled=\"{'true' if enabled else 'false'}\""
            if focused is not None:
                extra_attrs += f" focused=\"{'true' if focused else 'false'}\""
            if selected is not None:
                extra_attrs += f" selected=\"{'true' if selected else 'false'}\""
            if checked is not None:
                extra_attrs += f" checked=\"{'true' if checked else 'false'}\""
            if scrollable is not None:
                extra_attrs += f" scrollable=\"{'true' if scrollable else 'false'}\""
            lines.append(
                "  <node "
                f"index=\"{idx}\" "
                f"text=\"{_xml_escape(text)}\" "
                f"resource-id=\"{_xml_escape(res_id)}\" "
                f"class=\"{_xml_escape(_clean_text(el.get('class')) or 'android.view.View')}\" "
                f"package=\"{_xml_escape(pkg)}\" "
                f"content-desc=\"{_xml_escape(desc)}\" "
                f"clickable=\"{clickable}\""
                f"{extra_attrs}"
                f" bounds=\"{bounds}\" />"
            )
            if idx + 1 >= self._max_elements:
                break
        lines.append("</hierarchy>")
        return "\n".join(lines) + "\n"

    def _from_a11y_tree(
        self, tree: Dict[str, Any], *, default_package: Optional[str]
    ) -> UiElementsExtraction:
        errors: List[str] = []
        nodes = tree.get("nodes")
        if not isinstance(nodes, list):
            return UiElementsExtraction(
                source="a11y",
                ui_elements=[],
                errors=["a11y_nodes_missing"],
            )

        out: List[Dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue

            bbox = _normalize_bbox(
                node.get("bounds")
                or node.get("bbox")
                or node.get("bounds_in_screen")
                or node.get("boundsInScreen")
            )
            if bbox is None:
                continue

            text = _clean_text(node.get("text") or node.get("label"))
            desc = _clean_text(
                node.get("desc")
                or node.get("content_desc")
                or node.get("contentDescription")
                or node.get("content-desc")
            )
            resource_id = _clean_text(
                node.get("resource_id") or node.get("resource-id") or node.get("resourceId")
            )

            if not any((text, desc, resource_id)):
                continue

            clickable = _safe_bool(node.get("clickable"))
            if clickable is None:
                clickable = False

            pkg = _clean_text(node.get("package") or node.get("packageName")) or default_package
            pkg = _clean_text(pkg) or ""

            el: Dict[str, Any] = {
                "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
                "clickable": bool(clickable),
                "package": pkg,
                "text": text,
                "desc": desc,
                "resource_id": resource_id,
            }

            for out_key, candidates in (
                ("enabled", ("enabled", "is_enabled", "isEnabled")),
                ("focused", ("focused", "is_focused", "isFocused")),
                ("selected", ("selected", "is_selected", "isSelected")),
                ("checked", ("checked", "is_checked", "isChecked")),
                ("scrollable", ("scrollable", "is_scrollable", "isScrollable")),
            ):
                val = None
                for cand in candidates:
                    if cand in node:
                        val = _safe_bool(node.get(cand))
                        break
                if val is not None:
                    el[out_key] = bool(val)

            # Keep these optional fields if present (debugging / provenance).
            if "id" in node:
                el["node_id"] = _clean_text(node.get("id"))
            if "role" in node:
                el["role"] = _clean_text(node.get("role"))

            out.append(el)
            if len(out) >= self._max_elements:
                errors.append("max_elements_truncated")
                break

        return UiElementsExtraction(source="a11y", ui_elements=out, errors=errors)

    def _from_uiautomator_xml(
        self, xml_text: str, *, default_package: Optional[str]
    ) -> UiElementsExtraction:
        errors: List[str] = []
        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            return UiElementsExtraction(
                source="uiautomator",
                ui_elements=[],
                errors=[f"xml_parse:{type(e).__name__}"],
            )

        out: List[Dict[str, Any]] = []
        for node in root.iter():
            if node.tag != "node":
                continue
            attrs = node.attrib or {}
            bbox = _parse_uiautomator_bounds(attrs.get("bounds"))
            if bbox is None:
                continue

            text = _clean_text(attrs.get("text"))
            desc = _clean_text(attrs.get("content-desc") or attrs.get("contentDescription"))
            resource_id = _clean_text(attrs.get("resource-id"))

            if not any((text, desc, resource_id)):
                continue

            clickable = _safe_bool(attrs.get("clickable"))
            if clickable is None:
                clickable = False

            pkg = _clean_text(attrs.get("package")) or default_package
            pkg = _clean_text(pkg) or ""

            el: Dict[str, Any] = {
                "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
                "clickable": bool(clickable),
                "package": pkg,
                "text": text,
                "desc": desc,
                "resource_id": resource_id,
            }
            for out_key, attr_key in (
                ("enabled", "enabled"),
                ("focused", "focused"),
                ("selected", "selected"),
                ("checked", "checked"),
                ("scrollable", "scrollable"),
            ):
                val = _safe_bool(attrs.get(attr_key))
                if val is not None:
                    el[out_key] = bool(val)
            cls = _clean_text(attrs.get("class"))
            if cls:
                el["class"] = cls
            out.append(el)
            if len(out) >= self._max_elements:
                errors.append("max_elements_truncated")
                break

        return UiElementsExtraction(source="uiautomator", ui_elements=out, errors=errors)


def _xml_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
