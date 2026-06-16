#!/usr/bin/env bash
# Single deploy action for the Pi supervisor. Runs ON the Pi:
#
#   ssh <user>@<pi-host> 'cd power-system && bash scripts/deploy.sh'
#
# Pulls the repo to git truth, renders config templates
# (@OFFGRID_USER@/@PROJECT_DIR@) into their system locations, reinstalls
# the package if the manifest changed, restarts services, and
# health-checks the result. This is the only deploy verb; rsync of
# individual files is for bench iteration only and must end with a
# deploy.sh run so the checkout matches git.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${OFFGRID_PROJECT_DIR:-$(dirname "${SCRIPT_DIR}")}"
VENV="${PROJECT_DIR}/.venv"
# Interactive/desktop user: owns the checkout, the venv, and the console
# tmux session (which the desktop session must be able to attach to).
OFFGRID_USER="$(id -un)"
# Unprivileged account the supervisor and exporter run as. No shell, no
# sudo; dialout+gpio for the RS485 adapters and the ambient sensor.
SERVICE_USER="${OFFGRID_SERVICE_USER:-offgrid}"

render() {
    sed "s|@OFFGRID_USER@|${OFFGRID_USER}|g; s|@SERVICE_USER@|${SERVICE_USER}|g; s|@PROJECT_DIR@|${PROJECT_DIR}|g" "$1"
}

install_operator_sudoers() {
    local tmp
    tmp="$(mktemp /tmp/offgrid-sudoers.XXXXXX)"
    render config/sudoers/offgrid-operator-nopasswd > "${tmp}"
    chmod 440 "${tmp}"
    sudo visudo -cf "${tmp}" >/dev/null
    sudo install -o root -g root -m 440 "${tmp}" /etc/sudoers.d/020_offgrid_operator
    rm -f "${tmp}"
}

# The pull below may update this very script while bash is reading it
# incrementally. Run from a temp copy so the executing code can't change
# mid-deploy; the updated script applies on the next run.
if [ -z "${OFFGRID_DEPLOY_REEXEC:-}" ]; then
    tmp="$(mktemp /tmp/offgrid-deploy.XXXXXX.sh)"
    cp "$0" "${tmp}"
    chmod +x "${tmp}"
    OFFGRID_DEPLOY_REEXEC=1 OFFGRID_PROJECT_DIR="${PROJECT_DIR}" exec bash "${tmp}" "$@"
fi

cd "${PROJECT_DIR}"

echo "== git =="
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: working tree has local modifications:" >&2
    git status --short >&2
    echo "Reconcile first (commit on the workstation and push, then discard here with: git checkout -- .)" >&2
    exit 1
fi
old_head="$(git rev-parse HEAD)"
git pull --ff-only
new_head="$(git rev-parse HEAD)"
echo "${old_head:0:7} -> ${new_head:0:7}"

echo "== package =="
if ! git diff --quiet "${old_head}" "${new_head}" -- pyproject.toml; then
    echo "pyproject.toml changed; reinstalling package"
    "${VENV}/bin/pip" install -q -e .
else
    echo "manifest unchanged"
fi

echo "== sudoers =="
install_operator_sudoers

echo "== service account =="
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    sudo adduser --system --group --home /var/lib/offgrid --no-create-home "${SERVICE_USER}"
fi
sudo usermod -aG dialout,gpio "${SERVICE_USER}"
# Traverse-only ACL so the service account can reach the checkout and venv
# under the deploy user's (otherwise 700) home. Files inside are world-readable.
setfacl -m "u:${SERVICE_USER}:--x" "${HOME}"
# Secrets env file is loaded by systemd as root; nothing else should read it.
if [ -f /etc/offgrid-power.env ]; then
    sudo chown root:root /etc/offgrid-power.env
    sudo chmod 600 /etc/offgrid-power.env
fi

echo "== configs =="
# Render @OFFGRID_USER@/@SERVICE_USER@/@PROJECT_DIR@ templates for this host.
mkdir -p "${HOME}/.local/bin" "${HOME}/.config/autostart"
render config/systemd/offgrid-supervisor.service | sudo tee /etc/systemd/system/offgrid-supervisor.service > /dev/null
render config/systemd/offgrid-console.service | sudo tee /etc/systemd/system/offgrid-console.service > /dev/null
render config/systemd/offgrid-metrics-export.service | sudo tee /etc/systemd/system/offgrid-metrics-export.service > /dev/null
render config/systemd/offgrid-can-watchdog.service | sudo tee /etc/systemd/system/offgrid-can-watchdog.service > /dev/null
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
render config/systemd/getty-tty1-autologin.conf | sudo tee /etc/systemd/system/getty@tty1.service.d/offgrid-autologin.conf > /dev/null
sudo install -m 644 config/systemd/offgrid-can-watchdog.timer /etc/systemd/system/
sudo install -m 644 config/systemd/offgrid-metrics-export.timer /etc/systemd/system/
sudo install -m 644 config/nginx/offgrid-supervisor.conf /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/offgrid-supervisor.conf /etc/nginx/sites-enabled/offgrid-supervisor.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo install -m 644 config/udev/90-offgrid-usb.rules /etc/udev/rules.d/
install -m 755 config/desktop/open-offgrid-console "${HOME}/.local/bin/open-offgrid-console"
install -m 755 config/desktop/offgrid-tty-console "${HOME}/.local/bin/offgrid-tty-console"
install -m 755 config/desktop/offgrid-console-font "${HOME}/.local/bin/offgrid-console-font"
render config/desktop/offgrid-console.desktop > "${HOME}/.config/autostart/offgrid-console.desktop"
if ! grep -q 'offgrid-tty-console' "${HOME}/.profile" 2>/dev/null; then
    cat >> "${HOME}/.profile" <<'EOF'

# Off-grid wall display: the tty1 autologin session becomes the console
# (composed console: display pane + ready shell).
# See docs/runbooks/pi-boot-card-build.md, "Local Console Without a Desktop".
# Other ttys (Alt+F2...) stay normal shells.
if [ "$(tty)" = "/dev/tty1" ] && [ -z "${DISPLAY:-}" ] && [ -x "$HOME/.local/bin/offgrid-tty-console" ]; then
    exec "$HOME/.local/bin/offgrid-tty-console"
fi
EOF
fi
sudo systemctl daemon-reload
sudo systemctl enable --now --quiet offgrid-supervisor
sudo systemctl enable --now --quiet offgrid-console
sudo systemctl enable --now --quiet offgrid-can-watchdog.timer
sudo systemctl enable --now --quiet offgrid-metrics-export.timer
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty
sudo nginx -t -q

echo "== tests =="
PYTHONPATH="${PROJECT_DIR}/software/pi-controller/src" "${VENV}/bin/python" -m unittest discover -s tests -q

echo "== restart =="
sudo systemctl restart offgrid-supervisor
sudo systemctl reload nginx

echo "== health =="
sleep 6
sudo systemctl start offgrid-console
systemctl is-active offgrid-supervisor offgrid-console nginx
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/healthz)"
echo "supervisor /healthz: ${code}"
kindle="$(curl -s -o /dev/null -w '%{http_code}' -A 'Kindle/3.0' http://127.0.0.1:8080/)"
echo "kindle port via nginx: ${kindle}"
if [ "${kindle}" != "200" ]; then
    echo "ERROR: kindle port not serving 200" >&2
    exit 1
fi
echo "deploy OK at ${new_head:0:7}"
