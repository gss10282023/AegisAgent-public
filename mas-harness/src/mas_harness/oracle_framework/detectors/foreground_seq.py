from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.detectors._jsonl import iter_jsonl_objects
from mas_harness.oracle_framework.types import Fact


def _nonempty_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _safe_int(value: Any) -> Optional[int]:
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


class ForegroundSeqDetector(Detector):
    detector_id = "foreground_seq"
    evidence_required = ("foreground_trace.jsonl",)
    produces_fact_ids = ("fact.foreground_pkg_seq",)

    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        _ = case_ctx
        evidence_dir: Path = Path(getattr(pack, "evidence_dir", pack))

        # Backward compatibility: some older bundles used a different name.
        path = evidence_dir / "foreground_trace.jsonl"
        if not path.exists():
            alt = evidence_dir / "foreground_app_trace.jsonl"
            if alt.exists():
                path = alt

        if not path.exists():
            return []

        changes: list[dict[str, Any]] = []
        pkgs_all: list[str] = []
        last_pkg: str | None = None
        refs: list[str] = [path.name]

        for line_no, obj in iter_jsonl_objects(path):
            pkg = _nonempty_str(obj.get("package"))
            if pkg is None:
                continue
            pkgs_all.append(pkg)

            if pkg != last_pkg:
                changes.append(
                    {
                        "line": line_no,
                        "step": _safe_int(obj.get("step")),
                        "package": pkg,
                        "activity": _nonempty_str(obj.get("activity")),
                    }
                )
                refs.append(f"{path.name}:L{line_no}")
                last_pkg = pkg

        unique_packages = sorted(set(pkgs_all))
        payload = {
            "event_count": len(pkgs_all),
            "change_count": len(changes),
            "changes": changes,
            "unique_packages": unique_packages,
            "first_package": changes[0]["package"] if changes else None,
            "last_package": changes[-1]["package"] if changes else None,
        }

        return [
            Fact(
                fact_id="fact.foreground_pkg_seq",
                oracle_source="device_query",
                evidence_refs=refs,
                payload=payload,
            )
        ]
