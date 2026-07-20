#!/usr/bin/env bash
# Health checks for the off-grid supervisor Pi.
set -euo pipefail

PROJECT_DIR="${OFFGRID_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

check() {
    printf '== %s ==\n' "$1"
    shift
    "$@"
}

check "architecture" sh -c 'uname -m; getconf LONG_BIT; hostname'
check "os" sh -c 'cat /etc/os-release | sed -n "1,6p"'
check "services" systemctl is-active offgrid-supervisor offgrid-console nginx
check "timers" systemctl is-active offgrid-can-watchdog.timer offgrid-metrics-export.timer
check "can0" ip -details link show can0
check "serial devices" sh -c 'ls -l /dev/epever-rs485 /dev/cubix-rs485 /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || true'
check "storage" sh -c 'df -h / /srv/telemetry 2>/dev/null || df -h /; ls -ld /srv/telemetry/data /srv/telemetry/logs /var/lib/offgrid'
check "supervisor healthz" curl -fsS http://127.0.0.1:8081/healthz
check "telemetry storage" sh -c "curl -fsS http://127.0.0.1:8081/api/v1/health | jq -e '.checks.telemetry.status == \"ok\"' >/dev/null"
check "nginx kindle path" sh -c "curl -fsS -A 'Kindle/3.0' http://127.0.0.1:8080/ >/dev/null"
check "api snapshot summary" sh -c "curl -fsS http://127.0.0.1:8081/api/v1/snapshot | '${PROJECT_DIR}/.venv/bin/python' '${PROJECT_DIR}/scripts/diag_api.py'"
