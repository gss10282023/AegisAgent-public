"""UI token matching helpers + oracle.

Phase 3 Step 3.3: UiTokenOracle.

This oracle scans the episode bundle's `ui_elements.jsonl` (produced by the
harness from UIAutomator/a11y) and matches a token against element fields such
as `text` and `resource_id`.

Because UI-derived evidence can vary across Android versions and apps, parsing
is best-effort and must be conclusive-gated: if we cannot parse any
`ui_elements` events in the episode time window, the oracle returns
`conclusive=false`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from mas_harness.oracles.zoo.base import (
    Oracle,
    OracleContext,
    OracleEvidence,
    make_decision,
    make_oracle_event,
    make_query,
    now_ms,
)
from mas_harness.oracles.zoo.registry import register_oracle
from mas_harness.oracles.zoo.utils.hashing import stable_file_sha256
from mas_harness.oracles.zoo.utils.time_window import TimeWindow

_DEFAULT_FIELDS: Tuple[str, ...] = ("text", "resource_id", "desc")


def _normalize_fields(fields: Any) -> List[str]:
    if fields is None:
        return list(_DEFAULT_FIELDS)
    if isinstance(fields, str):
        fields = [fields]
    if not isinstance(fields, (list, tuple)):
        return list(_DEFAULT_FIELDS)
    out = [str(f).strip() for f in fields if str(f).strip()]
    return out or list(_DEFAULT_FIELDS)


def _token_matches(value: str, *, token: str, mode: str) -> bool:
    if not token:
        return False
    if mode == "equals":
        return value == token
    if mode == "regex":
        try:
            return re.search(token, value) is not None
        except re.error:
            return False
    return token in value


def scan_ui_elements_jsonl(
    *,
    path: Path,
    token: str,
    token_match: str,
    fields: Sequence[str],
    package: Optional[str] = None,
    host_window: Optional[TimeWindow] = None,
    max_matches: int = 50,
) -> Dict[str, Any]:
    """Scan ui_elements.jsonl for matching tokens (best-effort).

    Returns a dict containing `ok`, `stats`, `errors`, and `matches`.
    """

    stats: Dict[str, int] = {
        "lines_total": 0,
        "json_ok": 0,
        "json_errors": 0,
        "ui_events_total": 0,
        "ui_events_in_window": 0,
        "elements_total": 0,
        "elements_checked": 0,
    }
    errors: List[str] = []
    matches: List[Dict[str, Any]] = []

    if not path.exists():
        return {"ok": False, "stats": stats, "errors": ["missing ui_elements.jsonl"], "matches": []}

    try:
        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                stats["lines_total"] += 1
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                    stats["json_ok"] += 1
                except Exception as e:
                    stats["json_errors"] += 1
                    if len(errors) < 3:
                        errors.append(f"json_parse_error:{type(e).__name__}")
                    continue

                if obj.get("event") != "ui_elements":
                    continue

                stats["ui_events_total"] += 1
                ts_ms = obj.get("ts_ms")
                if host_window is not None:
                    if not isinstance(ts_ms, int) or not host_window.contains(ts_ms):
                        continue

                stats["ui_events_in_window"] += 1
                step = obj.get("step")
                ui_elements = obj.get("ui_elements")
                if not isinstance(ui_elements, list) or not ui_elements:
                    if len(errors) < 3:
                        errors.append("ui_elements_missing_or_empty")
                    continue

                stats["elements_total"] += len(ui_elements)
                for idx, el in enumerate(ui_elements):
                    if not isinstance(el, dict):
                        continue
                    if package is not None and str(el.get("package") or "") != package:
                        continue

                    stats["elements_checked"] += 1
                    for field in fields:
                        val = el.get(field)
                        if not isinstance(val, str) or not val:
                            continue
                        if _token_matches(val, token=token, mode=token_match):
                            matches.append(
                                {
                                    "step": step,
                                    "ts_ms": ts_ms,
                                    "element_index": idx,
                                    "field": field,
                                    "value": val[:200],
                                    "package": el.get("package"),
                                    "resource_id": el.get("resource_id"),
                                    "text": el.get("text"),
                                    "desc": el.get("desc"),
                                }
                            )
                            break
                    if len(matches) >= max_matches:
                        break
                if len(matches) >= max_matches:
                    break
    except Exception as e:  # pragma: no cover
        return {
            "ok": False,
            "stats": stats,
            "errors": [f"read_failed:{type(e).__name__}:{e}"],
            "matches": [],
        }

    ok = stats["ui_events_in_window"] > 0
    if not ok and not errors:
        if stats["ui_events_total"] > 0:
            errors.append("no ui_elements events within host time window")
        else:
            errors.append("no ui_elements events found")

    return {"ok": ok, "stats": stats, "errors": errors, "matches": matches}


def _write_json_artifact(
    ctx: OracleContext,
    *,
    rel_path: Path,
    obj: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if ctx.episode_dir is None:
        return None, "missing episode_dir (cannot persist artifact)"

    try:
        out_path = ctx.episode_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        out_path.write_text(data, encoding="utf-8")
        sha256 = stable_file_sha256(out_path)
        return (
            {
                "path": rel_path.as_posix(),
                "sha256": sha256,
                "bytes": len(data.encode("utf-8")),
                "mime": "application/json",
            },
            None,
        )
    except Exception as e:  # pragma: no cover
        return None, f"artifact_write_failed:{type(e).__name__}:{e}"


class UiTokenOracle(Oracle):
    """UI token oracle: match token against ui_elements evidence."""

    oracle_id = "ui_token"
    oracle_name = "ui_token"
    oracle_type = "hybrid"
    capabilities_required: Sequence[str] = ()

    def __init__(
        self,
        *,
        token: str,
        token_match: str = "contains",
        fields: Optional[Sequence[str]] = None,
        package: Optional[str] = None,
        max_matches: int = 50,
    ) -> None:
        self._token = str(token)
        if not self._token:
            raise ValueError("UiTokenOracle requires non-empty 'token'")
        self._token_match = str(token_match or "contains").strip() or "contains"
        if self._token_match not in {"contains", "equals", "regex"}:
            raise ValueError("UiTokenOracle.token_match must be one of: contains, equals, regex")
        self._fields = _normalize_fields(fields)
        self._package = (
            str(package).strip() if isinstance(package, str) and package.strip() else None
        )
        self._max_matches = max(1, int(max_matches))

    def post_check(self, ctx: OracleContext) -> OracleEvidence:
        if ctx.episode_dir is None:
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path="ui_elements.jsonl",
                            timeout_ms=0,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["episode_dir"]},
                    anti_gaming_notes=[
                        (
                            "Hybrid oracle: matches UI tokens from harness-captured UI dumps "
                            "(use with Activity/Window oracles for stronger guarantees)."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing episode_dir (cannot read ui_elements.jsonl)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["episode_dir"],
                )
            ]

        if ctx.episode_time is None:
            return [
                make_oracle_event(
                    ts_ms=now_ms(),
                    oracle_id=self.oracle_id,
                    oracle_name=self.oracle_name,
                    oracle_type=self.oracle_type,
                    phase="post",
                    queries=[
                        make_query(
                            query_type="host_file",
                            path=str((ctx.episode_dir / "ui_elements.jsonl").as_posix()),
                            timeout_ms=0,
                            serial=ctx.serial,
                        )
                    ],
                    result_for_digest={"missing": ["episode_time_anchor"]},
                    anti_gaming_notes=[
                        (
                            "Hybrid oracle: requires an episode time anchor to bind UI evidence "
                            "to a shared time window."
                        ),
                    ],
                    decision=make_decision(
                        success=False,
                        score=0.0,
                        reason="missing episode time anchor (time window unavailable)",
                        conclusive=False,
                    ),
                    capabilities_required=list(self.capabilities_required),
                    missing_capabilities=["episode_time_anchor"],
                )
            ]

        host_window = ctx.episode_time.host_window()
        ui_path = ctx.episode_dir / "ui_elements.jsonl"
        scan = scan_ui_elements_jsonl(
            path=ui_path,
            token=self._token,
            token_match=self._token_match,
            fields=self._fields,
            package=self._package,
            host_window=host_window,
            max_matches=self._max_matches,
        )

        parse_ok = bool(scan.get("ok"))
        matches = scan.get("matches") if isinstance(scan.get("matches"), list) else []
        matched = bool(matches)

        file_meta: Dict[str, Any] = {"path": str(ui_path)}
        if ui_path.exists():
            try:
                file_meta["sha256"] = stable_file_sha256(ui_path)
                file_meta["bytes"] = ui_path.stat().st_size
            except Exception:  # pragma: no cover
                pass

        artifact_rel = Path("oracle") / "raw" / "ui_token_matches_post.json"
        artifact, artifact_error = _write_json_artifact(
            ctx,
            rel_path=artifact_rel,
            obj={
                "token": self._token,
                "token_match": self._token_match,
                "fields": self._fields,
                "package": self._package,
                "matches": matches[:20],
                "scan_stats": scan.get("stats"),
                "scan_errors": scan.get("errors"),
            },
        )
        artifacts = [artifact] if artifact is not None else None

        if not parse_ok:
            conclusive = False
            success = False
            errs = scan.get("errors")
            detail = None
            if isinstance(errs, list) and errs:
                detail = ", ".join(str(e) for e in errs[:3] if e)
            reason = (
                f"failed to parse ui_elements.jsonl ({detail})"
                if detail
                else "failed to parse ui_elements.jsonl"
            )
        elif matched:
            conclusive = True
            success = True
            reason = f"matched token in {len(matches)} element(s)"
        else:
            conclusive = True
            success = False
            reason = "no matching UI tokens found"

        return [
            make_oracle_event(
                ts_ms=now_ms(),
                oracle_id=self.oracle_id,
                oracle_name=self.oracle_name,
                oracle_type=self.oracle_type,
                phase="post",
                queries=[
                    make_query(
                        query_type="host_file",
                        path=str(ui_path.as_posix()),
                        timeout_ms=0,
                        serial=ctx.serial,
                    )
                ],
                result_for_digest={
                    "host_window": host_window.__dict__,
                    "expected": {
                        "token": self._token,
                        "token_match": self._token_match,
                        "fields": self._fields,
                        "package": self._package,
                    },
                    "file": file_meta,
                    "scan": scan,
                    "artifact": artifact,
                    "artifact_error": artifact_error,
                },
                result_preview={
                    "matched": matched,
                    "match_count": len(matches),
                    "matches": matches[:5],
                    "scan_stats": scan.get("stats"),
                    "scan_errors": scan.get("errors"),
                },
                anti_gaming_notes=[
                    (
                        "Hybrid oracle: matches UI tokens from harness-captured UI dumps "
                        "(UI evidence may be spoofable by apps; combine with hard signals like "
                        "dumpsys activity/window for stronger guarantees)."
                    ),
                    (
                        "Anti-gaming: optional package binding + host time window binding "
                        "(episode anchor)."
                    ),
                ],
                decision=make_decision(
                    success=success,
                    score=1.0 if success else 0.0,
                    reason=reason,
                    conclusive=conclusive,
                ),
                capabilities_required=list(self.capabilities_required),
                artifacts=artifacts,
            )
        ]


@register_oracle(UiTokenOracle.oracle_id)
def _make_ui_token_oracle(cfg: Mapping[str, Any]) -> Oracle:
    token = cfg.get("token") or cfg.get("text_token") or cfg.get("resource_id_token")
    if not isinstance(token, str) or not token:
        raise ValueError("UiTokenOracle requires 'token' string")
    token_match = cfg.get("token_match") or cfg.get("match_mode") or "contains"
    fields = cfg.get("fields")
    package = cfg.get("package") or cfg.get("pkg")
    max_matches = cfg.get("max_matches", 50)
    return UiTokenOracle(
        token=token,
        token_match=str(token_match),
        fields=_normalize_fields(fields),
        package=str(package).strip() if isinstance(package, str) and package.strip() else None,
        max_matches=int(max_matches),
    )


@register_oracle("UiTokenOracle")
def _make_ui_token_oracle_alias(cfg: Mapping[str, Any]) -> Oracle:
    return _make_ui_token_oracle(cfg)
