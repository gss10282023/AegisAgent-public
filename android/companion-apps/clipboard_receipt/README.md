# Clipboard Receipt (Companion App / IME)

This minimal Android app provides a stable clipboard "receipt" path for
clipboard-based success oracles on modern Android.

## What it does

- Provides an `InputMethodService` (IME) that registers a clipboard listener.
- On every clipboard change, appends a JSON entry to:
  - `/sdcard/Android/data/com.mas.clipboardreceipt/files/clipboard_receipt.json`
- Entry schema:
  - `set_time` (epoch ms), `token`, `source_pkg` (best-effort), plus `text` for debug.

The IME extracts `token` from clipboard text by matching underscore-style tokens
and preferring ones containing `"TOKEN"` (case-insensitive). If no token-like
substring is found, it stores the full clipboard text.

## Enable + select the IME (adb)

```bash
adb shell ime enable com.mas.clipboardreceipt/.ReceiptImeService
adb shell ime set com.mas.clipboardreceipt/.ReceiptImeService
```

## Build/install (no Gradle wrapper)

```bash
tools/gradle_dist/gradle-8.7/bin/gradle -p android/companion-apps/clipboard_receipt :app:assembleDebug
adb install -r -t android/companion-apps/clipboard_receipt/app/build/outputs/apk/debug/app-debug.apk
```

## Smoke test

From the repo root, run:

```bash
python3 -m mas_harness.tools.smoke_clipboard_receipt
```
