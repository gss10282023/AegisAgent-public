"""Clipboard receipt oracle (companion-app receipts via /sdcard JSON).

Phase 2 Step 6.2 (high ROI): clipboard state is hard to observe directly on
modern Android (background access restrictions). This oracle relies on a
companion app that listens for clipboard changes and writes a JSON receipt to
external storage.

Receipt schema (device-generated):
- set_time: epoch ms
- token: extracted token / clipboard text
- source_pkg: best-effort source package (may be null/empty)
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from mas_harness.oracles.zoo.files.sdcard_receipt import SdcardJsonReceiptOracle
from mas_harness.oracles.zoo.registry import register_oracle

DEFAULT_CLIPBOARD_RECEIPT_PATH = (
    "/sdcard/Android/data/com.mas.clipboardreceipt/files/clipboard_receipt.json"
)


class ClipboardReceiptOracle(SdcardJsonReceiptOracle):
    oracle_id = "clipboard_receipt"
    oracle_name = "clipboard_receipt"
    oracle_type = "hard"
    capabilities_required = ("adb_shell", "pull_file")

    def __init__(
        self,
        *,
        token: str,
        remote_path: str = DEFAULT_CLIPBOARD_RECEIPT_PATH,
        source_pkg: Optional[str] = None,
        clear_before_run: bool = True,
        timeout_ms: int = 15_000,
    ) -> None:
        tok = str(token or "")
        if not tok:
            raise ValueError("ClipboardReceiptOracle requires non-empty 'token' string")

        expected = {}
        src = str(source_pkg).strip() if isinstance(source_pkg, str) else ""
        if src:
            expected["source_pkg"] = src

        super().__init__(
            remote_path=str(remote_path),
            expected=expected,
            token=tok,
            token_path="token",
            token_match="equals",
            timestamp_path="set_time",
            use_file_mtime_fallback=False,
            clear_before_run=bool(clear_before_run),
            timeout_ms=int(timeout_ms),
        )


@register_oracle(ClipboardReceiptOracle.oracle_id)
def _make_clipboard_receipt(cfg: Mapping[str, Any]) -> ClipboardReceiptOracle:
    token = cfg.get("token")
    remote_path = cfg.get("remote_path") or cfg.get("path") or DEFAULT_CLIPBOARD_RECEIPT_PATH
    source_pkg = cfg.get("source_pkg") or cfg.get("pkg")

    if not isinstance(token, str) or not token:
        raise ValueError("ClipboardReceiptOracle requires 'token' string")

    return ClipboardReceiptOracle(
        token=str(token),
        remote_path=str(remote_path),
        source_pkg=str(source_pkg).strip() if isinstance(source_pkg, str) else None,
        clear_before_run=bool(cfg.get("clear_before_run", True) or cfg.get("clear_receipt", False)),
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
    )


@register_oracle("ClipboardReceiptOracle")
def _make_clipboard_receipt_alias(cfg: Mapping[str, Any]) -> ClipboardReceiptOracle:
    return _make_clipboard_receipt(cfg)
