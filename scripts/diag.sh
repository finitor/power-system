#!/bin/sh
# One-call system digest for troubleshooting. Designed to be cheap to read:
# every check is one line, counts instead of dumps. Run on the Pi:
#
#   ssh <user>@<pi-host> 'power-system/scripts/diag.sh'
set -u
PATH=/usr/sbin:/usr/bin:/sbin:/bin

echo "services: supervisor=$(systemctl is-active offgrid-supervisor) console=$(systemctl is-active offgrid-console) nginx=$(systemctl is-active nginx) can-watchdog-timer=$(systemctl is-active offgrid-can-watchdog.timer)"

# Working-tree state: surfaces marooned bench edits (changes made on the Pi
# that never made it back to git) on every diag, since this is the first move
# anyone runs. Offline-safe: ahead/behind are vs the last-fetched origin, no
# network fetch.
REPO="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if git -C "${REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    head="$(git -C "${REPO}" rev-parse --short HEAD)"
    modified="$(git -C "${REPO}" status --porcelain --untracked-files=no | grep -c .)"
    untracked="$(git -C "${REPO}" ls-files --others --exclude-standard | grep -c .)"
    behind="$(git -C "${REPO}" rev-list --count HEAD..@{u} 2>/dev/null || echo '?')"
    ahead="$(git -C "${REPO}" rev-list --count @{u}..HEAD 2>/dev/null || echo '?')"
    if [ "${modified}" -eq 0 ] && [ "${untracked}" -eq 0 ]; then
        tree="clean"
    else
        tree="DIRTY (${modified} modified, ${untracked} untracked) <- reconcile before deploy"
    fi
    echo "git: ${head} ${tree}; behind=${behind} ahead=${ahead} (vs last-fetched origin)"
else
    echo "git: not a repo"
fi

SYS=/sys/class/net/can0
if [ -d "${SYS}" ]; then
    rx1="$(cat "${SYS}/statistics/rx_packets")"
    sleep 3
    rx2="$(cat "${SYS}/statistics/rx_packets")"
    state="$(cat "${SYS}/operstate" 2>/dev/null || echo '?')"
    echo "can0: state=${state} frames_3s=$((rx2 - rx1)) rx_total=${rx2}"
else
    echo "can0: absent"
fi

echo "watchdog: $(journalctl -u offgrid-can-watchdog -n 1 --no-pager --output cat 2>/dev/null | tail -1 || echo 'no entries')"

curl -s --max-time 5 http://127.0.0.1:8081/api/v1/snapshot \
    | python3 "$(dirname "$0")/diag_api.py" || echo "api: unreachable"

DB=/srv/telemetry/data/metrics.sqlite
# The store is owned by the service account; a plain WAL read as the
# operator would fail on the -shm/-wal sidecars, so go through sudo.
echo "store: $(sudo -n sqlite3 -readonly "${DB}" "SELECT 'samples=' || COUNT(*) || ' newest=' || COALESCE(MAX(captured_at), 'none') FROM samples" 2>/dev/null || echo unreadable)"

echo "events: $(sudo -n sqlite3 -readonly "${DB}" "SELECT COALESCE(MAX(captured_at || ' ' || source || '/' || event), 'none logged') FROM events" 2>/dev/null || echo unreadable)"

echo "kindle-port: $(curl -s --max-time 5 -o /dev/null -w '%{http_code}' -A 'Kindle/3.0' http://127.0.0.1:8080/)"
