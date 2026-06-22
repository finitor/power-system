#!/usr/bin/env bash
# Restore selected files from a migration backup bundle.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/restore-config.sh [--apply] BACKUP.tar.gz

Without --apply, this only unpacks the bundle into a temporary staging
directory and prints the restore actions. With --apply, it restores local
config, telemetry data, service-adjacent config, and SSH material found in the
bundle. Full boot/root fstab entries and boot cmdline are preserved only as
reference files; review the backup before applying.
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

restore_reference() {
    local rel="$1"
    local dest="$2"
    if [ ! -e "${ROOT}/${rel}" ]; then
        echo "skip missing ${rel}"
        return
    fi
    echo "reference ${rel} -> ${dest}"
    if [ "${APPLY}" -eq 1 ]; then
        sudo mkdir -p "$(dirname "${dest}")"
        if [ -d "${ROOT}/${rel}" ]; then
            sudo rm -rf "${dest}"
            sudo mkdir -p "${dest}"
            sudo rsync -a "${ROOT}/${rel}/" "${dest}/"
        else
            sudo cp "${ROOT}/${rel}" "${dest}"
        fi
    fi
}

restore_telemetry_fstab_line() {
    local rel="etc/fstab"
    local line
    if [ ! -f "${ROOT}/${rel}" ]; then
        echo "skip missing ${rel}"
        return
    fi
    line="$(awk '$2 == "/srv/telemetry" { print; exit }' "${ROOT}/${rel}")"
    if [ -z "${line}" ]; then
        echo "skip missing /srv/telemetry fstab line"
        return
    fi
    echo "ensure /srv/telemetry fstab line"
    if [ "${APPLY}" -eq 1 ]; then
        if ! grep -Eq '[[:space:]]/srv/telemetry[[:space:]]' /etc/fstab; then
            printf '\n# Restored by off-grid migration restore.\n%s\n' "${line}" | sudo tee -a /etc/fstab > /dev/null
        else
            echo "/etc/fstab already has /srv/telemetry; leaving existing entry unchanged"
        fi
    fi
}

restore_path etc/offgrid-power.env /etc/offgrid-power.env 600
restore_reference etc/fstab /etc/fstab.offgrid-migration-reference
restore_telemetry_fstab_line
restore_path etc/hostname /etc/hostname 644
restore_path etc/hosts /etc/hosts 644
restore_path etc/cloud/cloud.cfg /etc/cloud/cloud.cfg 644
restore_path etc/cloud/templates/hosts.debian.tmpl /etc/cloud/templates/hosts.debian.tmpl 644
restore_path etc/udev/rules.d/90-offgrid-usb.rules /etc/udev/rules.d/90-offgrid-usb.rules 644
restore_path etc/nginx/sites-available/offgrid-supervisor.conf /etc/nginx/sites-available/offgrid-supervisor.conf 644
restore_path etc/ssh/sshd_config.d/10-local.conf /etc/ssh/sshd_config.d/10-local.conf 644
restore_reference etc/sudoers.d /etc/sudoers.d.offgrid-migration-reference
restore_path etc/NetworkManager/NetworkManager.conf /etc/NetworkManager/NetworkManager.conf 644
restore_tree etc/NetworkManager/system-connections /etc/NetworkManager/system-connections
restore_tree etc/systemd/network /etc/systemd/network
restore_tree etc/wpa_supplicant /etc/wpa_supplicant
restore_path etc/default/tailscaled /etc/default/tailscaled 644
restore_path etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/20auto-upgrades 644
restore_path etc/apt/apt.conf.d/52unattended-upgrades-local /etc/apt/apt.conf.d/52unattended-upgrades-local 644
restore_path etc/default/console-setup /etc/default/console-setup 644
restore_reference boot/firmware/config.txt /boot/firmware/config.txt.offgrid-migration-reference
restore_reference boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.offgrid-migration-reference
restore_path home/offgrid-user/.local/bin/open-offgrid-console "${HOME}/.local/bin/open-offgrid-console" 755
restore_path home/offgrid-user/.local/bin/offgrid-tty-console "${HOME}/.local/bin/offgrid-tty-console" 755
restore_path home/offgrid-user/.local/bin/offgrid-console-font "${HOME}/.local/bin/offgrid-console-font" 755
restore_path home/offgrid-user/.local/state/offgrid/console-font-index "${HOME}/.local/state/offgrid/console-font-index" 644
restore_path home/offgrid-user/.profile "${HOME}/.profile" 644
restore_path home/offgrid-user/.config/autostart/offgrid-console.desktop "${HOME}/.config/autostart/offgrid-console.desktop" 644
restore_tree srv/telemetry /srv/telemetry
restore_tree var/lib/offgrid /var/lib/offgrid
restore_tree var/lib/tailscale /var/lib/tailscale

if [ -e "${ROOT}/home/offgrid-user/.ssh" ]; then
    echo "restore tree home/offgrid-user/.ssh -> ${HOME}/.ssh"
    if [ "${APPLY}" -eq 1 ]; then
        mkdir -p "${HOME}/.ssh"
        rsync -a "${ROOT}/home/offgrid-user/.ssh/" "${HOME}/.ssh/"
        chmod 700 "${HOME}/.ssh"
        find "${HOME}/.ssh" -type f -exec chmod 600 {} \;
        # Ensure GitHub uses the deploy key; write config if none was in the backup.
        if [ -f "${HOME}/.ssh/blueberry_deploy" ] && [ ! -f "${HOME}/.ssh/config" ]; then
            cat > "${HOME}/.ssh/config" <<'SSHEOF'
Host github.com
    IdentityFile ~/.ssh/blueberry_deploy
    IdentitiesOnly yes
SSHEOF
            chmod 600 "${HOME}/.ssh/config"
            echo "wrote ~/.ssh/config for github.com -> blueberry_deploy"
        fi
    fi
fi

if [ "${APPLY}" -eq 1 ]; then
    chown -R "$(id -u):$(id -g)" "${HOME}/.local" "${HOME}/.config" "${HOME}/.profile" 2>/dev/null || true
    sudo chown -R "$(id -u):$(id -g)" /srv/telemetry /var/lib/offgrid
    if [ -d /var/lib/tailscale ]; then
        sudo chown -R root:root /var/lib/tailscale
        sudo chmod 700 /var/lib/tailscale
    fi
    if [ -d /etc/NetworkManager/system-connections ]; then
        sudo chmod 700 /etc/NetworkManager/system-connections
        sudo find /etc/NetworkManager/system-connections -type f -exec chmod 600 {} \;
    fi
    sudo systemctl daemon-reload
    sudo udevadm control --reload-rules
    if command -v nginx >/dev/null 2>&1; then
        sudo nginx -t
    else
        echo "nginx not installed yet; skipping nginx -t"
    fi
    echo "Restore applied. Run scripts/deploy.sh next."
else
    echo "Dry run only. Re-run with --apply to restore."
fi
