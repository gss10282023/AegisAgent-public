# Notification Listener Receipt (Companion App)

This minimal Android app enables a stable "receipt" path for notification-based
success oracles on modern Android.

## What it does

- Runs a `NotificationListenerService`.
- On every posted notification, appends a JSON entry to:
  - `/sdcard/Android/data/com.mas.notificationlistenerreceipt/files/notification_receipt.json`
- Entry schema:
  - `pkg`, `title`, `text`, `post_time` (epoch ms), `token_hit`

`token_hit` is best-effort extracted from `title/text` by matching underscore-style
tokens and preferring ones containing `"TOKEN"` (case-insensitive).

## Enable the listener (adb)

```bash
adb shell cmd notification allow_listener \
  com.mas.notificationlistenerreceipt/com.mas.notificationlistenerreceipt.ReceiptNotificationListenerService
```

## Build/install (no Gradle wrapper)

This repo does not commit a Gradle wrapper JAR. Use a locally downloaded Gradle:

```bash
tools/gradle_dist/gradle-8.7/bin/gradle -p android/companion-apps/notification_listener_receipt :app:assembleDebug
adb install -r -t android/companion-apps/notification_listener_receipt/app/build/outputs/apk/debug/app-debug.apk
```

## Smoke test

From the repo root, run:

```bash
python3 -m mas_harness.tools.smoke_notification_listener_receipt
```
