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
    build-essential \
    can-utils \
    curl \
    git \
    iproute2 \
    nginx \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    rsync \
    tmux \
    usbutils

echo "== directories =="
mkdir -p "${HOME}/.local/bin" "${HOME}/.config/autostart"
sudo mkdir -p /srv/telemetry/data /srv/telemetry/logs /var/lib/offgrid
sudo chown -R "$(id -u):$(id -g)" /srv/telemetry /var/lib/offgrid

echo "== python environment =="
if [ ! -d "${VENV}" ]; then
    python3 -m venv "${VENV}"
fi
"${VENV}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV}/bin/pip" install -e "${PROJECT_DIR}"
if [ "${OFFGRID_INSTALL_SENSOR_EXTRAS:-1}" = "1" ]; then
    "${VENV}/bin/pip" install -e "${PROJECT_DIR}[sensors]"
fi

echo "== deploy =="
"${PROJECT_DIR}/scripts/deploy.sh"
