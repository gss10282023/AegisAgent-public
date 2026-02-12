#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [emulator_host] $*"
}

ANDROID_SERIAL="${ANDROID_SERIAL:-emulator-5554}"
BOOT_TIMEOUT_S="${BOOT_TIMEOUT_S:-900}"
CHROME_DISABLE_FRE="${CHROME_DISABLE_FRE:-1}"
CHROME_COMMAND_LINE_FLAGS="${CHROME_COMMAND_LINE_FLAGS:---disable-fre --disable-signin-promo}"
CHROME_COMMAND_LINE_FILE="${CHROME_COMMAND_LINE_FILE:-/data/local/tmp/chrome-command-line}"
CHROME_PACKAGE="${CHROME_PACKAGE:-com.android.chrome}"

if ! command -v adb >/dev/null 2>&1; then
  log "ERROR: adb not found in PATH."
  exit 127
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EMULATOR_PORT=""
if [[ "${ANDROID_SERIAL}" == emulator-* ]]; then
  EMULATOR_PORT="${ANDROID_SERIAL#emulator-}"
fi
EMULATOR_LOG_FILE=""
if [[ -n "${EMULATOR_PORT}" && "${EMULATOR_PORT}" =~ ^[0-9]+$ ]]; then
  candidate="${SCRIPT_DIR}/logs/emulator-${EMULATOR_PORT}.log"
  if [[ -f "${candidate}" ]]; then
    EMULATOR_LOG_FILE="${candidate}"
  fi
fi

log "Waiting for device to be online: ${ANDROID_SERIAL}"
adb -s "${ANDROID_SERIAL}" wait-for-device >/dev/null

log "Waiting for sys.boot_completed=1 (timeout=${BOOT_TIMEOUT_S}s)..."
deadline=$((SECONDS + BOOT_TIMEOUT_S))
while true; do
  # adb can temporarily return non-zero during early boot (offline/transient). Do not fail fast.
  boot_complete="$(adb -s "${ANDROID_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
  if [[ "${boot_complete}" == "1" ]]; then
    log "Boot completed."
    break
  fi
  if (( SECONDS >= deadline )); then
    log "ERROR: timed out waiting for boot completion."
    log "adb devices:"
    adb devices || true
    if [[ -n "${EMULATOR_LOG_FILE}" ]]; then
      log "emulator log tail (${EMULATOR_LOG_FILE}):"
      tail -n 200 "${EMULATOR_LOG_FILE}" || true
    fi
    exit 1
  fi
  sleep 2
done

if [[ "${CHROME_DISABLE_FRE}" == "1" ]]; then
  log "Configuring Chrome to skip First Run Experience (FRE)..."

  adb -s "${ANDROID_SERIAL}" shell "am force-stop ${CHROME_PACKAGE}" >/dev/null 2>&1 || true

  if printf "chrome %s\n" "${CHROME_COMMAND_LINE_FLAGS}" | adb -s "${ANDROID_SERIAL}" shell "cat > ${CHROME_COMMAND_LINE_FILE}" >/dev/null 2>&1; then
    adb -s "${ANDROID_SERIAL}" shell "chmod 644 ${CHROME_COMMAND_LINE_FILE}" >/dev/null 2>&1 || true
    adb -s "${ANDROID_SERIAL}" shell "ls -la ${CHROME_COMMAND_LINE_FILE}" >/dev/null 2>&1 || true
    log "Chrome command line: ${CHROME_COMMAND_LINE_FLAGS}"
  else
    log "WARNING: failed to write ${CHROME_COMMAND_LINE_FILE}; Chrome may show onboarding UI."
  fi
fi
