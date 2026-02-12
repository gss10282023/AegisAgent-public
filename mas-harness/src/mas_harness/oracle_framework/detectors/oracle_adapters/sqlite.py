from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mas_harness.evidence import stable_sha256
from mas_harness.oracle_framework.detectors.oracle_adapters.registry import register_adapter
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


@register_adapter("SqlitePullQueryAdapter", priority=80)
class SqlitePullQueryAdapter:
    adapter_name = "SqlitePullQueryAdapter"
    priority = 80

    def matches(self, event: dict[str, Any]) -> bool:
        name = _nonempty_str(event.get("oracle_name"))
        return name in {"sqlite_pull_query", "sqlite_root_query"}

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]:
        _ = evidence_dir
        queries = event.get("queries")
        sql = None
        remote_path = None
        if isinstance(queries, list):
            for q in queries:
                if not isinstance(q, dict):
                    continue
                if sql is None and _nonempty_str(q.get("sql")) is not None:
                    sql = _nonempty_str(q.get("sql"))
                if remote_path is None and _nonempty_str(q.get("path")) is not None:
                    remote_path = _nonempty_str(q.get("path"))

        preview = event.get("result_preview")
        row_count = None
        if isinstance(preview, dict) and isinstance(preview.get("row_count"), int):
            row_count = int(preview.get("row_count"))

        payload = {
            "oracle_name": _nonempty_str(event.get("oracle_name")),
            "phase": _nonempty_str(event.get("phase")),
            "decision": event.get("decision") if isinstance(event.get("decision"), dict) else None,
            "db_path_hash": stable_sha256(remote_path) if remote_path is not None else None,
            "query_hash": stable_sha256(sql) if sql is not None else None,
            "row_count": row_count,
            "preview_hash": stable_sha256(preview) if preview is not None else None,
            "result_digest": _nonempty_str(event.get("result_digest")),
        }
        return [
            Fact(
                fact_id=f"fact.sqlite.query_result_summary/L{int(line_no)}",
                oracle_source="device_query",
                evidence_refs=[],
                payload=payload,
            )
        ]
