#!/usr/bin/env sh
set -eu

SESSION_NAME="${OFFGRID_CONSOLE_SESSION:-offgrid-console}"
CLASSIC_HOST="${CLASSIC_HOST:-192.168.0.10}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${OFFGRID_PROJECT_DIR:-$(dirname "${SCRIPT_DIR}")}"
PYTHON="${OFFGRID_PYTHON:-${PROJECT_DIR}/.venv/bin/python}"
DISPLAY_MODULE="${OFFGRID_DISPLAY_MODULE:-offgrid_power.cli.api_terminal_display}"
DISPLAY_URL="${OFFGRID_DISPLAY_URL:-http://127.0.0.1:8081/api/v1/snapshot}"
DISPLAY_INTERVAL="${OFFGRID_DISPLAY_INTERVAL:-5}"
ERROR_LOG="${OFFGRID_CONSOLE_ERROR_LOG:-/srv/offgrid/logs/terminal-display.err.log}"

stop_console() {
  /usr/bin/tmux kill-session -t "${SESSION_NAME}" 2>/dev/null || true
  exit 0
}

trap stop_console TERM INT

/bin/mkdir -p "$(dirname "${ERROR_LOG}")"

/usr/bin/tmux new-session -d -s "${SESSION_NAME}" \
  "/bin/sh -lc 'cd ${PROJECT_DIR} && PYTHONPATH=${PROJECT_DIR}/software/pi-controller/src ${PYTHON} -m ${DISPLAY_MODULE} --url ${DISPLAY_URL} --interval ${DISPLAY_INTERVAL} 2>>${ERROR_LOG}; status=\$?; date -Is >>${ERROR_LOG}; printf \"terminal display exited with status %s\n\" \"\$status\" >>${ERROR_LOG}; exit \"\$status\"'"

while /usr/bin/tmux has-session -t "${SESSION_NAME}" 2>/dev/null; do
  sleep 5
done

date -Is >&2
echo "tmux session ${SESSION_NAME} exited" >&2
exit 1
