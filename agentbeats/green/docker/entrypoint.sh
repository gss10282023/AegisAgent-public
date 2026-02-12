#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [green] $*"
}

AVD_NAME="${AVD_NAME:-mas_avd}"
CONSOLE_PORT="${EMULATOR_CONSOLE_PORT:-5556}"
ADB_PORT="${EMULATOR_ADB_PORT:-5557}"
FORWARD_PORT="${EMULATOR_ADB_FORWARD_PORT:-5555}"
BOOT_TIMEOUT_S="${EMULATOR_BOOT_TIMEOUT_S:-600}"

cleanup() {
  set +e
  if [[ -n "${EMU_PID:-}" ]]; then
    kill "${EMU_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${SOCAT_PID:-}" ]]; then
    kill "${SOCAT_PID}" >/dev/null 2>&1 || true
  fi
  wait >/dev/null 2>&1 || true
}

trap cleanup EXIT SIGTERM SIGINT

log "Starting adb server..."
adb start-server >/dev/null

log "Starting adb TCP forward: 0.0.0.0:${FORWARD_PORT} -> 127.0.0.1:${ADB_PORT}"
socat -d -d "TCP-LISTEN:${FORWARD_PORT},bind=0.0.0.0,reuseaddr,fork" "TCP:127.0.0.1:${ADB_PORT}" &
SOCAT_PID=$!

ACCEL="off"
if [[ -c /dev/kvm ]]; then
  ACCEL="on"
fi

log "Starting emulator: avd=${AVD_NAME} console_port=${CONSOLE_PORT} adb_port=${ADB_PORT} accel=${ACCEL}"
emulator \
  -avd "${AVD_NAME}" \
  -port "${CONSOLE_PORT}" \
  -no-window \
  -no-audio \
  -no-boot-anim \
  -gpu swiftshader_indirect \
  -accel "${ACCEL}" \
  -wipe-data \
  -no-snapshot-save \
  -no-snapshot-load \
  >/tmp/emulator.log 2>&1 &
EMU_PID=$!

SERIAL="emulator-${CONSOLE_PORT}"

log "Waiting for device to appear (${SERIAL})..."
adb -s "${SERIAL}" wait-for-device >/dev/null

log "Waiting for boot completion (timeout=${BOOT_TIMEOUT_S}s)..."
deadline=$((SECONDS + BOOT_TIMEOUT_S))
while true; do
  boot_complete="$(adb -s "${SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
  if [[ "${boot_complete}" == "1" ]]; then
    break
  fi
  if (( SECONDS >= deadline )); then
    log "ERROR: emulator did not boot within ${BOOT_TIMEOUT_S}s"
    log "emulator log tail:"
    tail -n 200 /tmp/emulator.log || true
    exit 1
  fi
  sleep 2
done

log "Emulator booted. adb devices:"
adb devices || true

exec "$@"

