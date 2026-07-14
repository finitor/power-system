#!/usr/bin/env bash
# Bootstrap a fresh Raspberry Pi OS Lite 64-bit install for this project.
set -euo pipefail

PROJECT_DIR="${OFFGRID_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV="${PROJECT_DIR}/.venv"

if [ "$(id -u)" -eq 0 ]; then
    echo "Run as the normal offgrid user, not root." >&2
    exit 1
fi

echo "== os packages =="
sudo apt-get update
sudo apt-get install -y \
    acl \
    build-essential \
    can-utils \
    curl \
    git \
    iproute2 \
    jq \
    nginx \
    nodejs \
    npm \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    rsync \
    sqlite3 \
    tmux \
    usbutils

if [ "${OFFGRID_INSTALL_TAILSCALE:-1}" = "1" ] && ! command -v tailscale >/dev/null 2>&1; then
    echo "== tailscale =="
    curl -fsSL https://tailscale.com/install.sh | sh
fi
if command -v tailscale >/dev/null 2>&1; then
    sudo systemctl enable --now tailscaled
fi

echo "== directories =="
mkdir -p "${HOME}/.local/bin" "${HOME}/.config/autostart"
# Ownership is handled by deploy.sh (service account) and the supervisor
# unit's ExecStartPre chown; bootstrap only needs the directories to exist.
sudo mkdir -p /srv/telemetry/data /srv/telemetry/logs /var/lib/offgrid

echo "== python environment =="
if [ ! -d "${VENV}" ]; then
    python3 -m venv "${VENV}"
fi
"${VENV}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV}/bin/pip" install -e "${PROJECT_DIR}"
if [ "${OFFGRID_INSTALL_SENSOR_EXTRAS:-1}" = "1" ]; then
    "${VENV}/bin/pip" install -e "${PROJECT_DIR}[sensors]"
fi

if [ "${OFFGRID_INSTALL_CLAUDE_CLI:-1}" = "1" ] && ! command -v claude >/dev/null 2>&1; then
    echo "== claude cli =="
    sudo npm install -g @anthropic-ai/claude-code
    echo "Claude CLI installed. Run 'claude' to authenticate."
fi

echo "== deploy =="
"${PROJECT_DIR}/scripts/deploy.sh"
# offgrid group is created by deploy.sh (adduser --system --group); add operator now.
sudo usermod -aG offgrid "$USER"
# Allow the operator to create SQLite lock files without immutable=1 workaround.
sudo chmod g+w /srv/telemetry/data
