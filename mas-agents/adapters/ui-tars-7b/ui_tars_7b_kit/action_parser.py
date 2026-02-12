# action_parser.py
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

"""
Step 1 目标：把官方模板输出的文本（Thought/Action）解析成结构化动作。
注意：
- 每一步仅解析 1 条动作（与官方模板一致）。
- 坐标为「模型输出的绝对像素」原样透传，不做缩放/偏移处理（Step 2 再处理）。
"""


# ---------- 数据结构 ----------
@dataclass
class MobileAction:
    type: str
    params: Dict[str, Any]


@dataclass
class ParsedOutput:
    thought: str
    actions: List[MobileAction]  # 目前 1 条，保留列表形态便于以后扩展
    raw_action: str  # 原始 Action 文本，便于调试/日志


# ---------- 工具 ----------
_POINT_RE = re.compile(
    r"<point>\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*</point>",
    re.IGNORECASE | re.DOTALL,
)


def _extract_point(s: str) -> Optional[Tuple[float, float]]:
    """从字符串中提取第一个 <point>x y</point>，返回 (x, y)"""
    if not s:
        return None
    m = _POINT_RE.search(s)
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    return (x, y)


def _extract_str_arg(name: str, call: str) -> Optional[str]:
    """
    提取类似 name='...'(或 "...") 的入参，并用 literal_eval 还原转义。
    例如：content='UI-TARS\\n' → 返回 'UI-TARS\n'
    """
    m = re.search(rf"{name}\s*=\s*(['\"])(.*?)\1", call, re.DOTALL)
    if not m:
        return None
    quote, inner = m.group(1), m.group(2)

    s = f"{quote}{inner}{quote}"
    try:
        return ast.literal_eval(s)
    except SyntaxError:
        # 兼容不小心塞进真实换行/回车/Tab 的情况
        inner2 = inner.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return ast.literal_eval(f"{quote}{inner2}{quote}")


def _func_name(call: str) -> Optional[str]:
    m = re.match(r"\s*([a-zA-Z_]\w*)\s*\(", call)
    return m.group(1) if m else None


def _inside_parens(call: str) -> str:
    """
    提取函数名后的括号内文本（可能为空）。
    例如：click(point='<point>1 2</point>') → 返回 "point='<point>1 2</point>'"
    """
    m = re.match(r"\s*[a-zA-Z_]\w*\s*\((.*)\)\s*$", call, re.DOTALL)
    return m.group(1).strip() if m else ""


# --- 最小增量：兼容 '(x,y)' 形式（如 start_box='(546,1167)' 或 point="(100,200)"） ---
def _extract_xy_tuple_arg(name: str, call: str) -> Optional[Tuple[float, float]]:
    """
    从调用串中提取形如 name='(x,y)' 或 name="(x,y)" 的参数，返回 (x, y)
    仅作为兜底，不影响原有 <point> 解析逻辑。
    """
    m = re.search(rf"{name}\s*=\s*(['\"])\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)\1", call)
    if not m:
        return None
    return float(m.group(2)), float(m.group(3))


# ---------- 主解析 ----------
def parse_mobile_output(text: str) -> ParsedOutput:
    """
    解析如下格式：
      Thought: ...
      Action: click(point='<point>x y</point>')
    返回 ParsedOutput(thought, [MobileAction(...)] , raw_action)
    """
    text = text.strip()

    # Thought：允许缺省；匹配到 Action: 之前的部分
    m_thought = re.search(
        r"Thought:\s*(.+?)(?=\n\s*Action\s*:|$)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    thought = m_thought.group(1).strip() if m_thought else ""

    # Action：必须存在
    m_action = re.search(r"Action\s*:\s*(.+)$", text, re.DOTALL | re.IGNORECASE)
    if not m_action:
        raise ValueError("未找到 Action: 段落")
    raw_action = m_action.group(1).strip()

    # 仅解析第一条函数调用（官方模板“每步一动”）
    func = _func_name(raw_action)
    if not func:
        raise ValueError(f"无法识别的动作函数：{raw_action}")

    args_text = _inside_parens(raw_action)
    params: Dict[str, Any] = {}

    if func in ("click", "long_press"):
        # 先尝试在整个参数串里找 <point>
        pt = _extract_point(args_text)

        # 若没有，再尝试按参数名依次找：point / start_point / start_box
        if pt is None:
            for name in ("point", "start_point", "start_box"):
                s = _extract_str_arg(name, raw_action)
                if s:
                    pt = _extract_point(s)
                    if pt:
                        break
                if pt is None:
                    xy = _extract_xy_tuple_arg(name, raw_action)
                    if xy:
                        pt = xy
                        break

        # 仍找不到，则作为兜底再扫一遍 <point>（已做过，一般不会到这里）
        if pt is None:
            all_pts = _POINT_RE.findall(args_text)
            if all_pts:
                pt = (float(all_pts[0][0]), float(all_pts[0][1]))

        if pt is None:
            raise ValueError(f"{func} 需要 point（或 start_point/start_box）")

        params["point"] = pt

    elif func == "type":
        content = _extract_str_arg("content", raw_action)
        if content is None:
            raise ValueError("type 需要 content='...'")
        params["content"] = content

    elif func == "scroll":
        # <point> 优先
        pt = _extract_point(args_text)

        # 若无，则依次尝试 point / start_point / start_box
        if pt is None:
            for name in ("point", "start_point", "start_box"):
                s = _extract_str_arg(name, raw_action)
                if s:
                    pt = _extract_point(s)
                    if pt:
                        break
                if pt is None:
                    xy = _extract_xy_tuple_arg(name, raw_action)
                    if xy:
                        pt = xy
                        break

        direction = _extract_str_arg("direction", raw_action)
        if pt is None or direction is None:
            raise ValueError("scroll 需要 point 与 direction")
        direction = direction.strip().lower()
        if direction not in ("up", "down", "left", "right"):
            raise ValueError("scroll 的 direction 仅支持 up/down/left/right")
        params["point"] = pt
        params["direction"] = direction

    elif func == "open_app":
        app = _extract_str_arg("app_name", raw_action)
        if app is None:
            raise ValueError("open_app 需要 app_name='包名或应用名'")
        params["app_name"] = app

    elif func == "hotkey":
        # 兼容 hotkey(key='enter') 或 hotkey(hotkey='enter')
        key = _extract_str_arg("key", raw_action) or _extract_str_arg("hotkey", raw_action)
        if not key:
            raise ValueError("hotkey 需要 key='...'")
        params["key"] = key.strip().lower()

    elif func == "drag":
        # 优先：严格按模板读取 <point>
        sp_raw = _extract_str_arg("start_point", raw_action)
        ep_raw = _extract_str_arg("end_point", raw_action)
        sp = _extract_point(sp_raw or "")
        ep = _extract_point(ep_raw or "")

        # 新增兜底：支持 start_point='(x,y)' / end_point='(x,y)'
        if sp is None:
            sp = _extract_xy_tuple_arg("start_point", raw_action)
        if ep is None:
            ep = _extract_xy_tuple_arg("end_point", raw_action)

        # 最小兜底：兼容 start_box / end_box='(x,y)' 或其中包含 <point>
        if sp is None:
            sb_raw = _extract_str_arg("start_box", raw_action) or ""
            sp = _extract_point(sb_raw) or _extract_xy_tuple_arg("start_box", raw_action)
        if ep is None:
            eb_raw = _extract_str_arg("end_box", raw_action) or ""
            ep = _extract_point(eb_raw) or _extract_xy_tuple_arg("end_box", raw_action)

        # 仍不满足则再看看括号里是否至少有两个 <point>
        if sp is None or ep is None:
            all_pts = _POINT_RE.findall(args_text)
            if len(all_pts) >= 2:
                sp = sp or (float(all_pts[0][0]), float(all_pts[0][1]))
                ep = ep or (float(all_pts[1][0]), float(all_pts[1][1]))

        if sp is None or ep is None:
            raise ValueError("drag 需要 start_point 与 end_point（两个 <point>）")

        params["start_point"] = sp
        params["end_point"] = ep

    elif func in ("press_home", "press_back"):
        pass

    elif func == "finished":
        content = _extract_str_arg("content", raw_action) or ""
        params["content"] = content

    elif func == "wait":
        # 模型输出 Action: wait()；不解析数值参数，执行器里用预设秒数等待
        # 因此这里不往 params 填内容
        pass

    else:
        raise ValueError(f"未支持的动作类型：{func}")

    action = MobileAction(type=func, params=params)
    return ParsedOutput(thought=thought, actions=[action], raw_action=raw_action)

