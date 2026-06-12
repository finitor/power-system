#!/usr/bin/env bash
# Restore selected files from a migration backup bundle.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/restore-config.sh [--apply] BACKUP.tar.gz

Without --apply, this only unpacks the bundle into a temporary staging
directory and prints the restore actions. With --apply, it restores the
environment file, telemetry data, service-adjacent config, and SSH directory
found in the bundle. Review the backup before applying.
EOF
}

APPLY=0
if [ "${1:-}" = "--apply" ]; then
    APPLY=1
    shift
fi

if [ "$#" -ne 1 ]; then
    usage >&2
    exit 2
fi

ARCHIVE="$1"
if [ ! -f "${ARCHIVE}" ]; then
    echo "Backup archive not found: ${ARCHIVE}" >&2
    exit 1
fi

STAGE="$(mktemp -d /tmp/offgrid-restore.XXXXXX)"
trap 'rm -rf "${STAGE}"' EXIT
tar -C "${STAGE}" -xzf "${ARCHIVE}"
ROOT="$(find "${STAGE}" -mindepth 1 -maxdepth 1 -type d | head -1)"

restore_path() {
    local rel="$1"
    local dest="$2"
    local mode="${3:-}"
    if [ ! -e "${ROOT}/${rel}" ]; then
        echo "skip missing ${rel}"
        return
    fi
    echo "restore ${rel} -> ${dest}"
    if [ "${APPLY}" -eq 1 ]; then
        sudo mkdir -p "$(dirname "${dest}")"
        sudo cp -a "${ROOT}/${rel}" "${dest}"
        if [ -n "${mode}" ]; then
            sudo chmod "${mode}" "${dest}"
        fi
    fi
}

restore_tree() {
    local rel="$1"
    local dest="$2"
    if [ ! -e "${ROOT}/${rel}" ]; then
        echo "skip missing ${rel}"
        return
    fi
    echo "restore tree ${rel} -> ${dest}"
    if [ "${APPLY}" -eq 1 ]; then
        sudo mkdir -p "${dest}"
        sudo rsync -a "${ROOT}/${rel}/" "${dest}/"
    fi
}

restore_path etc/offgrid-power.env /etc/offgrid-power.env 600
restore_path etc/udev/rules.d/90-offgrid-usb.rules /etc/udev/rules.d/90-offgrid-usb.rules 644
restore_path etc/nginx/sites-available/offgrid-supervisor.conf /etc/nginx/sites-available/offgrid-supervisor.conf 644
restore_path home/offgrid-user/.local/bin/open-offgrid-console "${HOME}/.local/bin/open-offgrid-console" 755
restore_path home/offgrid-user/.config/autostart/offgrid-console.desktop "${HOME}/.config/autostart/offgrid-console.desktop" 644
restore_tree srv/telemetry /srv/telemetry
restore_tree var/lib/offgrid /var/lib/offgrid

if [ -e "${ROOT}/home/offgrid-user/.ssh" ]; then
    echo "restore tree home/offgrid-user/.ssh -> ${HOME}/.ssh"
    if [ "${APPLY}" -eq 1 ]; then
        mkdir -p "${HOME}/.ssh"
        rsync -a "${ROOT}/home/offgrid-user/.ssh/" "${HOME}/.ssh/"
        chmod 700 "${HOME}/.ssh"
        find "${HOME}/.ssh" -type f -exec chmod 600 {} \;
    fi
fi

if [ "${APPLY}" -eq 1 ]; then
    sudo chown -R "$(id -u):$(id -g)" /srv/telemetry /var/lib/offgrid
    sudo systemctl daemon-reload
    sudo udevadm control --reload-rules
    sudo nginx -t
    echo "Restore applied. Run scripts/deploy.sh next."
else
    echo "Dry run only. Re-run with --apply to restore."
fi
