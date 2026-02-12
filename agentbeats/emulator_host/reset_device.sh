#!/usr/bin/env bash
set -euo pipefail

ANDROID_SERIAL="${ANDROID_SERIAL:-emulator-5554}"

# Optional hook: add your own benchmark-specific reset here.
# Keep it safe/idempotent. Examples:
# - return to home screen
# - clear recent apps

adb -s "${ANDROID_SERIAL}" shell input keyevent 3 >/dev/null 2>&1 || true

