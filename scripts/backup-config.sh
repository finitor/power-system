#!/usr/bin/env bash
# Create a migration backup bundle on the Raspberry Pi.
set -euo pipefail

PROJECT_DIR="${OFFGRID_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BACKUP_ROOT="${OFFGRID_BACKUP_ROOT:-${HOME}/offgrid-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
HOST="$(hostname -s 2>/dev/null || hostname)"
BUNDLE_DIR="${BACKUP_ROOT}/offgrid-${HOST}-${STAMP}"
ARCHIVE="${BUNDLE_DIR}.tar.gz"

mkdir -p "${BUNDLE_DIR}"

copy_if_exists() {
    local src="$1"
    local dest="$2"
    if sudo test -e "${src}"; then
        mkdir -p "$(dirname "${BUNDLE_DIR}/${dest}")"
        sudo cp -a "${src}" "${BUNDLE_DIR}/${dest}"
    fi
}

capture_cmd() {
    local dest="$1"
    shift
    mkdir -p "$(dirname "${BUNDLE_DIR}/${dest}")"
    if "$@" > "${BUNDLE_DIR}/${dest}" 2>&1; then
        return 0
    fi
    printf 'command failed: %s\n' "$*" >> "${BUNDLE_DIR}/${dest}"
}

echo "Creating backup in ${BUNDLE_DIR}"

copy_if_exists /etc/offgrid-power.env etc/offgrid-power.env
copy_if_exists /etc/systemd/system/offgrid-supervisor.service etc/systemd/system/offgrid-supervisor.service
copy_if_exists /etc/systemd/system/offgrid-console.service etc/systemd/system/offgrid-console.service
copy_if_exists /etc/systemd/system/offgrid-metrics-export.service etc/systemd/system/offgrid-metrics-export.service
copy_if_exists /etc/systemd/system/offgrid-metrics-export.timer etc/systemd/system/offgrid-metrics-export.timer
copy_if_exists /etc/systemd/system/offgrid-can-watchdog.service etc/systemd/system/offgrid-can-watchdog.service
copy_if_exists /etc/systemd/system/offgrid-can-watchdog.timer etc/systemd/system/offgrid-can-watchdog.timer
copy_if_exists /etc/udev/rules.d/90-offgrid-usb.rules etc/udev/rules.d/90-offgrid-usb.rules
copy_if_exists /etc/nginx/sites-available/offgrid-supervisor.conf etc/nginx/sites-available/offgrid-supervisor.conf
copy_if_exists "${HOME}/.local/bin/open-offgrid-console" home/offgrid-user/.local/bin/open-offgrid-console
copy_if_exists "${HOME}/.config/autostart/offgrid-console.desktop" home/offgrid-user/.config/autostart/offgrid-console.desktop
copy_if_exists "${HOME}/.ssh" home/offgrid-user/.ssh
copy_if_exists /srv/telemetry srv/telemetry
copy_if_exists /var/lib/offgrid var/lib/offgrid

capture_cmd manifest/hostname.txt hostnamectl
capture_cmd manifest/uname.txt uname -a
capture_cmd manifest/os-release.txt cat /etc/os-release
capture_cmd manifest/dpkg-selections.txt dpkg --get-selections
capture_cmd manifest/offgrid-unit-files.txt systemctl list-unit-files 'offgrid-*'
capture_cmd manifest/offgrid-units.txt systemctl status --no-pager 'offgrid-*'
capture_cmd manifest/timers.txt systemctl list-timers --all
capture_cmd manifest/ip-addr.txt ip addr
capture_cmd manifest/ip-link.txt ip -details link
capture_cmd manifest/lsusb.txt lsusb
capture_cmd manifest/serial-devices.txt sh -c 'ls -l /dev/*rs485 /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || true'
capture_cmd manifest/mounts.txt findmnt
capture_cmd manifest/df.txt df -h
capture_cmd manifest/git-head.txt git -C "${PROJECT_DIR}" rev-parse HEAD
capture_cmd manifest/git-status.txt git -C "${PROJECT_DIR}" status --short

cat > "${BUNDLE_DIR}/manifest/README.txt" <<EOF
Off-grid power Pi migration backup
Host: ${HOST}
UTC: ${STAMP}
Project: ${PROJECT_DIR}

This bundle is intended for the Raspberry Pi OS Lite 64-bit migration.
Review contents before restoring secrets or overwriting system files.
EOF

sudo chown -R "$(id -u):$(id -g)" "${BUNDLE_DIR}"
tar -C "${BACKUP_ROOT}" -czf "${ARCHIVE}" "$(basename "${BUNDLE_DIR}")"
rm -rf "${BUNDLE_DIR}"

echo "${ARCHIVE}"
