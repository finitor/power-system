#!/usr/bin/env sh
set -eu

SESSION_NAME="${OFFGRID_CONSOLE_SESSION:-offgrid-console}"
CLASSIC_HOST="${CLASSIC_HOST:-192.168.0.10}"
PROJECT_DIR="${OFFGRID_PROJECT_DIR:-/home/@OFFGRID_USER@/power-system}"
PYTHON="${OFFGRID_PYTHON:-${PROJECT_DIR}/.venv/bin/python}"
DISPLAY_SCRIPT="${OFFGRID_DISPLAY_SCRIPT:-${PROJECT_DIR}/scripts/supervisor-display.py}"
ERROR_LOG="${OFFGRID_CONSOLE_ERROR_LOG:-${PROJECT_DIR}/data/terminal-display.err.log}"

stop_console() {
  /usr/bin/tmux kill-session -t "${SESSION_NAME}" 2>/dev/null || true
  exit 0
}

trap stop_console TERM INT

/bin/mkdir -p "$(dirname "${ERROR_LOG}")"

/usr/bin/tmux new-session -d -s "${SESSION_NAME}" \
  "/bin/sh -lc 'cd ${PROJECT_DIR} && ${PYTHON} ${DISPLAY_SCRIPT} --classic-host ${CLASSIC_HOST} --interval 5 2>>${ERROR_LOG}; status=\$?; date -Is >>${ERROR_LOG}; printf \"terminal display exited with status %s\n\" \"\$status\" >>${ERROR_LOG}; exit \"\$status\"'"

while /usr/bin/tmux has-session -t "${SESSION_NAME}" 2>/dev/null; do
  sleep 5
done

date -Is >&2
echo "tmux session ${SESSION_NAME} exited" >&2
exit 1
