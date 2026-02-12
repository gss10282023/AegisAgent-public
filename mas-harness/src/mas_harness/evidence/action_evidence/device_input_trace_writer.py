from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from mas_harness.evidence import EvidenceWriter


class DeviceInputTraceWriter:
    """Minimal writer for device_input_trace.jsonl.

    Reuses `EvidenceWriter.record_device_input_event` for contract validation.
    """

    record_device_input_event = EvidenceWriter.record_device_input_event

    def __init__(self, path: Path, *, mode: str = "w") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._files = {"device_input": self.path.open(mode, encoding="utf-8")}
        self._last_device_input_step_idx: Optional[int] = None

    def _write_line(self, stream: str, obj: Dict[str, Any]) -> None:
        if stream != "device_input":
            raise ValueError(f"unsupported stream: {stream}")
        f = self._files[stream]
        f.write(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        f.write("\n")
        f.flush()

    def close(self) -> None:
        try:
            self._files["device_input"].close()
        except Exception:
            pass

    def __enter__(self) -> "DeviceInputTraceWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()
