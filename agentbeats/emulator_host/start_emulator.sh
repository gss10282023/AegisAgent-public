#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [emulator_host] $*"
}

AVD_NAME="${AVD_NAME:-mas_avd}"
CONSOLE_PORT="${EMULATOR_CONSOLE_PORT:-5554}"
SERIAL="emulator-${CONSOLE_PORT}"
AVD_HOME="${ANDROID_AVD_HOME:-${HOME}/.android/avd}"

# 1 = headless (-no-window)
HEADLESS="${HEADLESS:-1}"
# 1 = wipe userdata on boot (-wipe-data)
WIPE_DATA="${WIPE_DATA:-0}"
# timeout waiting for the emulator device to appear in `adb devices`
DEVICE_APPEAR_TIMEOUT_S="${DEVICE_APPEAR_TIMEOUT_S:-180}"
# extra emulator args (space-separated)
EMULATOR_ARGS="${EMULATOR_ARGS:-}"

if ! command -v adb >/dev/null 2>&1; then
  log "ERROR: adb not found in PATH."
  exit 127
fi

AVD_INI="${AVD_HOME}/${AVD_NAME}.ini"
AVD_DIR_DEFAULT="${AVD_HOME}/${AVD_NAME}.avd"
if [[ -f "${AVD_INI}" ]]; then
  AVD_DIR_FROM_INI="$(sed -n 's/^path=//p' "${AVD_INI}" | head -n 1 || true)"
  if [[ -n "${AVD_DIR_FROM_INI}" && ! -d "${AVD_DIR_FROM_INI}" && -d "${AVD_DIR_DEFAULT}" ]]; then
    log "WARNING: ${AVD_INI} points to missing path: ${AVD_DIR_FROM_INI}"
    log "Fixing to: ${AVD_DIR_DEFAULT}"
    if ! command -v python3 >/dev/null 2>&1; then
      log "ERROR: python3 is required to auto-fix ${AVD_INI}."
      log "Please edit ${AVD_INI} to point to: ${AVD_DIR_DEFAULT}"
      exit 1
    fi
    python3 - <<PY
from __future__ import annotations

from pathlib import Path

ini = Path("${AVD_INI}")
avd_name = "${AVD_NAME}"
default_dir = Path("${AVD_DIR_DEFAULT}")

lines = ini.read_text(encoding="utf-8", errors="replace").splitlines()
out: list[str] = []
for line in lines:
    if line.startswith("path="):
        out.append(f"path={default_dir}")
        continue
    if line.startswith("path.rel="):
        out.append(f"path.rel=avd/{avd_name}.avd")
        continue
    out.append(line)
ini.write_text("\\n".join(out) + "\\n", encoding="utf-8")
print(f"Updated {ini}: path -> {default_dir}")
PY
  fi
fi

if adb devices | awk -v s="${SERIAL}" '$1==s {found=1} END{exit found?0:1}'; then
  log "${SERIAL} already present; skip starting a new emulator."
  adb devices || true
  exit 0
fi

EMULATOR_BIN="emulator"
if ! command -v "${EMULATOR_BIN}" >/dev/null 2>&1; then
  if [[ -n "${ANDROID_SDK_ROOT:-}" && -x "${ANDROID_SDK_ROOT}/emulator/emulator" ]]; then
    EMULATOR_BIN="${ANDROID_SDK_ROOT}/emulator/emulator"
  elif [[ -n "${ANDROID_HOME:-}" && -x "${ANDROID_HOME}/emulator/emulator" ]]; then
    EMULATOR_BIN="${ANDROID_HOME}/emulator/emulator"
  else
    log "ERROR: emulator not found in PATH (and ANDROID_SDK_ROOT/ANDROID_HOME not set)."
    log "Install Android SDK Emulator and ensure 'emulator' is available."
    exit 127
  fi
fi

if ! "${EMULATOR_BIN}" -list-avds | awk -v avd="${AVD_NAME}" '$0==avd {found=1} END{exit found?0:1}'; then
  log "ERROR: Unknown AVD name [${AVD_NAME}]."
  log "Available AVDs:"
  "${EMULATOR_BIN}" -list-avds || true
  log "Searched AVD home: ${AVD_HOME}"
  ls -la "${AVD_HOME}" 2>/dev/null || true
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/emulator-${CONSOLE_PORT}.log"

args=(
  -avd "${AVD_NAME}"
  -port "${CONSOLE_PORT}"
  -no-audio
  -no-boot-anim
  -gpu swiftshader_indirect
  -no-snapshot-load
  -no-snapshot-save
)
if [[ "${HEADLESS}" == "1" ]]; then
  args+=(-no-window)
fi
if [[ "${WIPE_DATA}" == "1" ]]; then
  args+=(-wipe-data)
fi

log "Starting emulator: avd=${AVD_NAME} port=${CONSOLE_PORT} headless=${HEADLESS} wipe_data=${WIPE_DATA}"
log "Log: ${LOG_FILE}"

# shellcheck disable=SC2086
nohup "${EMULATOR_BIN}" "${args[@]}" ${EMULATOR_ARGS} >"${LOG_FILE}" 2>&1 &

log "Waiting for ${SERIAL} to appear (timeout=${DEVICE_APPEAR_TIMEOUT_S}s)..."
deadline=$((SECONDS + DEVICE_APPEAR_TIMEOUT_S))
while true; do
  if adb devices | awk -v s="${SERIAL}" '$1==s {found=1} END{exit found?0:1}'; then
    log "Device appeared: ${SERIAL}"
    break
  fi
  if (( SECONDS >= deadline )); then
    log "ERROR: device did not appear within ${DEVICE_APPEAR_TIMEOUT_S}s"
    log "emulator log tail:"
    tail -n 200 "${LOG_FILE}" || true
    exit 1
  fi
  sleep 1
done
