#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [emulator_host] $*"
}

ADB_PORT="${ADB_PORT:-5037}"

if ! command -v adb >/dev/null 2>&1; then
  log "ERROR: adb not found in PATH."
  log "Install Android Platform Tools and ensure 'adb' is available."
  exit 127
fi

log "Restarting adb server with -a on port ${ADB_PORT}..."
adb kill-server >/dev/null 2>&1 || true
adb -a -P "${ADB_PORT}" start-server >/dev/null

log "adb server started. Local devices:"
adb -P "${ADB_PORT}" devices || true

log "NOTE: 'adb -a' exposes the server on network interfaces."
log "Ensure port ${ADB_PORT} is only reachable from trusted networks/CI."
