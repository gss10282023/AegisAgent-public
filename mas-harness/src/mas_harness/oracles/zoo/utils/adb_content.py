"""ADB content-provider helpers for Oracle Zoo.

Provider-backed oracles (sms/contacts/calendar/calllog/mediastore) share a lot of
boilerplate around:
  - building `content query` shell commands
  - running them via controller.adb_shell with different controller signatures
  - producing a consistent meta dict and a conservative "ok" verdict
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, List, Mapping, Optional


def content_query_cmd(
    *,
    uri: str,
    projection: Optional[str] = None,
    where: Optional[str] = None,
    sort: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    parts: List[str] = ["content", "query", "--uri", str(uri)]
    if projection:
        parts += ["--projection", str(projection)]
    if where:
        parts += ["--where", str(where)]
    if sort:
        parts += ["--sort", str(sort)]
    if limit is not None:
        parts += ["--limit", str(int(limit))]
    return " ".join(shlex.quote(p) for p in parts)


def _first_nonempty_line(text: str) -> str | None:
    for line in str(text or "").splitlines():
        s = line.strip()
        if s:
            return s
    return None


def _strip_limit_from_cmd(cmd: str) -> str:
    try:
        parts = shlex.split(str(cmd or ""))
    except Exception:
        return str(cmd or "")

    out: list[str] = []
    skip_next = False
    for p in parts:
        if skip_next:
            skip_next = False
            continue
        if p == "--limit":
            skip_next = True
            continue
        out.append(p)

    return " ".join(shlex.quote(p) for p in out)


def _meta_looks_like_unsupported_limit(meta: Mapping[str, Any]) -> bool:
    stdout = str(meta.get("stdout", "") or "")
    stderr = str(meta.get("stderr", "") or "")
    combined = (stdout + "\n" + stderr).lower()
    return (
        "unsupported argument: --limit" in combined
        or "unknown option: --limit" in combined
        or "unknown argument: --limit" in combined
    )


def _adb_result_to_meta(res: Any) -> Dict[str, Any]:
    if hasattr(res, "stdout") or hasattr(res, "returncode"):
        return {
            "args": getattr(res, "args", None),
            "returncode": getattr(res, "returncode", None),
            "stderr": getattr(res, "stderr", None),
            "stdout": str(getattr(res, "stdout", "") or ""),
        }
    return {"args": None, "returncode": 0, "stderr": None, "stdout": str(res)}


def run_content_query(controller: Any, *, cmd: str, timeout_ms: int) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"cmd": cmd, "timeout_ms": int(timeout_ms)}

    res: Any = None
    exc: str | None = None
    try:
        try:
            res = controller.adb_shell(cmd, timeout_ms=timeout_ms, check=False)
        except TypeError:
            res = controller.adb_shell(cmd, timeout_s=float(timeout_ms) / 1000.0, check=False)
    except TypeError:
        res = controller.adb_shell(cmd)
    except Exception as e:  # pragma: no cover
        exc = repr(e)

    if res is not None:
        meta.update(_adb_result_to_meta(res))
    else:
        meta.update({"args": None, "returncode": None, "stderr": None, "stdout": ""})

    if exc:
        meta["exception"] = exc

    if "--limit" in str(cmd or "") and _meta_looks_like_unsupported_limit(meta):
        fallback_cmd = _strip_limit_from_cmd(str(cmd or ""))
        if fallback_cmd and fallback_cmd != str(cmd or ""):
            fallback_meta = run_content_query(controller, cmd=fallback_cmd, timeout_ms=timeout_ms)
            fallback_meta["fallback"] = {
                "kind": "unsupported_limit",
                "cmd_original": str(cmd or ""),
            }
            return fallback_meta

    return meta


def content_query_meta_ok(meta: Mapping[str, Any]) -> bool:
    rc = meta.get("returncode")
    if isinstance(rc, int) and rc != 0:
        return False

    stdout = str(meta.get("stdout", "") or "")
    stderr = str(meta.get("stderr", "") or "")
    combined = (stdout + "\n" + stderr).lower()

    first = _first_nonempty_line(combined)
    if first is not None:
        if first.startswith("usage:"):
            return False
        if first.startswith("[error]"):
            return False

    if "permission denial" in combined or "securityexception" in combined:
        return False
    if combined.strip().startswith("error:"):
        return False
    if _meta_looks_like_unsupported_limit(meta):
        return False
    return True


def content_query_error_kind(meta: Mapping[str, Any]) -> str | None:
    if content_query_meta_ok(meta):
        return None

    rc = meta.get("returncode")
    stdout = str(meta.get("stdout", "") or "")
    stderr = str(meta.get("stderr", "") or "")
    combined = (stdout + "\n" + stderr).lower()

    if "permission denial" in combined or "securityexception" in combined:
        return "permission_denied"
    if combined.strip().startswith("error:"):
        return "content_tool_error"
    if isinstance(rc, int) and rc != 0:
        return "nonzero_returncode"
    if meta.get("exception"):
        return "exception"
    return "unknown_error"
