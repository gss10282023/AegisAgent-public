#!/usr/bin/env bash
set -euo pipefail

CONSOLE_PORT="${EMULATOR_CONSOLE_PORT:-5556}"
SERIAL="emulator-${CONSOLE_PORT}"

adb -s "${SERIAL}" wait-for-device >/dev/null
boot_complete="$(adb -s "${SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
[[ "${boot_complete}" == "1" ]]

