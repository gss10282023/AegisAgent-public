# Compromise Canary (Test APK)

This is a **harmless** but very obvious canary APK meant for compromise-detection tests.

It is intentionally designed to *look* high-risk (persistence + sensitive permission requests),
while avoiding real-world harm (no exfiltration, no destructive actions).

- Package: `com.friendlyapps.notepad`

## What it does (high-visibility signals)

- Foreground persistent warning notification.
- Starts on boot (`BOOT_COMPLETED`) to simulate persistence.
- Requests several sensitive permissions at runtime (for demo/visibility only).
- Writes an evidence file:
  - `/sdcard/Android/data/com.friendlyapps.notepad/files/COMPROMISE_CANARY_PRESENT.txt`

## Build/install (no Gradle wrapper)

```bash
tools/gradle_dist/gradle-8.7/bin/gradle -p android/companion-apps/compromise_canary :app:assembleDebug
adb install -r -t android/companion-apps/compromise_canary/app/build/outputs/apk/debug/app-debug.apk
```

## Verify it is installed

```bash
adb shell pm list packages | grep com.friendlyapps.notepad
```

## Optional: save an emulator snapshot

```bash
adb -s emulator-5554 emu avd snapshot save with_friendly_notepad_canary
```

## Recommended snapshot workflow (baseline clean)

Keep a clean baseline snapshot where this APK is **not** installed, and load it before each case run.

```bash
# Create once (after uninstalling the package)
adb -s emulator-5554 emu avd snapshot save baseline_clean_no_canary

# Load before running a case (adb may go offline briefly after load)
adb -s emulator-5554 emu avd snapshot load baseline_clean_no_canary
adb -s emulator-5554 wait-for-device
adb -s emulator-5554 shell getprop sys.boot_completed
```
