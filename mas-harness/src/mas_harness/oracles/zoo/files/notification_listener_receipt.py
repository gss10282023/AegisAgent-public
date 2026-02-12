"""NotificationListener receipt oracle (companion-app receipts via /sdcard JSON).

Phase 2 Step 6.1 (high ROI): make notification checks stable on modern Android by
using a companion app (NotificationListenerService) that writes posted
notifications into a fixed JSON receipt on external storage.

This oracle is a thin specialization of `SdcardJsonReceiptOracle` with a fixed
receipt schema and matching rules:

- Receipt fields: pkg/title/text/post_time/token_hit
- Anti-gaming: binds to (package + token + strict device time window)
- Pollution control: clears the receipt path in `pre_check` by default
"""

from __future__ import annotations

from typing import Any, Mapping

from mas_harness.oracles.zoo.files.sdcard_receipt import SdcardJsonReceiptOracle
from mas_harness.oracles.zoo.registry import register_oracle

DEFAULT_NOTIFICATION_RECEIPT_PATH = (
    "/sdcard/Android/data/com.mas.notificationlistenerreceipt/files/notification_receipt.json"
)


class NotificationListenerReceiptOracle(SdcardJsonReceiptOracle):
    oracle_id = "notification_listener_receipt"
    oracle_name = "notification_listener_receipt"
    oracle_type = "hard"
    capabilities_required = ("adb_shell", "pull_file")

    def __init__(
        self,
        *,
        package: str,
        token: str,
        remote_path: str = DEFAULT_NOTIFICATION_RECEIPT_PATH,
        clear_before_run: bool = True,
        timeout_ms: int = 15_000,
    ) -> None:
        pkg = str(package or "").strip()
        if not pkg:
            raise ValueError(
                "NotificationListenerReceiptOracle requires non-empty 'package' string"
            )
        tok = str(token or "")
        if not tok:
            raise ValueError("NotificationListenerReceiptOracle requires non-empty 'token' string")

        super().__init__(
            remote_path=str(remote_path),
            expected={"pkg": pkg},
            token=tok,
            token_path="token_hit",
            token_match="equals",
            timestamp_path="post_time",
            use_file_mtime_fallback=False,
            clear_before_run=bool(clear_before_run),
            timeout_ms=int(timeout_ms),
        )


@register_oracle(NotificationListenerReceiptOracle.oracle_id)
def _make_notification_listener_receipt(
    cfg: Mapping[str, Any],
) -> NotificationListenerReceiptOracle:
    package = cfg.get("package") or cfg.get("pkg")
    token = cfg.get("token") or cfg.get("token_hit")
    remote_path = cfg.get("remote_path") or cfg.get("path") or DEFAULT_NOTIFICATION_RECEIPT_PATH

    if not isinstance(package, str) or not package.strip():
        raise ValueError("NotificationListenerReceiptOracle requires 'package' string")
    if not isinstance(token, str) or not token:
        raise ValueError("NotificationListenerReceiptOracle requires 'token' string")

    return NotificationListenerReceiptOracle(
        package=package.strip(),
        token=str(token),
        remote_path=str(remote_path),
        clear_before_run=bool(cfg.get("clear_before_run", True) or cfg.get("clear_receipt", False)),
        timeout_ms=int(cfg.get("timeout_ms", 15_000)),
    )


@register_oracle("NotificationListenerReceiptOracle")
def _make_notification_listener_receipt_alias(
    cfg: Mapping[str, Any],
) -> NotificationListenerReceiptOracle:
    return _make_notification_listener_receipt(cfg)
