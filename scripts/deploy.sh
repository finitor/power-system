#!/usr/bin/env bash
# Single deploy action for the Pi supervisor. Runs ON the Pi:
#
#   ssh @OFFGRID_USER@@blueberry.local power-system/scripts/deploy.sh
#
# Pulls the repo to git truth, syncs config files into their system
# locations, reinstalls the package if the manifest changed, restarts
# services, and health-checks the result. This is the only deploy verb;
# rsync of individual files is for bench iteration only and must end
# with a deploy.sh run so the checkout matches git.
set -euo pipefail

PROJECT_DIR="${OFFGRID_PROJECT_DIR:-/home/@OFFGRID_USER@/power-system}"
VENV="${PROJECT_DIR}/.venv"

# The pull below may update this very script while bash is reading it
# incrementally. Run from a temp copy so the executing code can't change
# mid-deploy; the updated script applies on the next run.
if [ -z "${OFFGRID_DEPLOY_REEXEC:-}" ]; then
    tmp="$(mktemp /tmp/offgrid-deploy.XXXXXX.sh)"
    cp "$0" "${tmp}"
    chmod +x "${tmp}"
    OFFGRID_DEPLOY_REEXEC=1 exec bash "${tmp}" "$@"
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

echo "== configs =="
sudo install -m 644 config/systemd/offgrid-supervisor.service /etc/systemd/system/
sudo install -m 644 config/systemd/offgrid-console.service /etc/systemd/system/
sudo install -m 644 config/systemd/offgrid-metrics-export.service /etc/systemd/system/
sudo install -m 644 config/systemd/offgrid-metrics-export.timer /etc/systemd/system/
sudo install -m 644 config/nginx/offgrid-supervisor.conf /etc/nginx/sites-available/
sudo install -m 644 config/udev/90-offgrid-usb.rules /etc/udev/rules.d/
install -m 755 config/desktop/open-offgrid-console "${HOME}/.local/bin/open-offgrid-console"
install -m 644 config/desktop/offgrid-console.desktop "${HOME}/.config/autostart/offgrid-console.desktop"
sudo systemctl daemon-reload
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
