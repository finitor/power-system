#!/bin/sh
# One-call system digest for troubleshooting. Designed to be cheap to read:
# every check is one line, counts instead of dumps. Run on the Pi:
#
#   ssh <user>@<pi-host> 'power-system/scripts/diag.sh'
set -u
PATH=/usr/sbin:/usr/bin:/sbin:/bin

echo "services: supervisor=$(systemctl is-active offgrid-supervisor) console=$(systemctl is-active offgrid-console) nginx=$(systemctl is-active nginx) can-watchdog-timer=$(systemctl is-active offgrid-can-watchdog.timer)"

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

echo "kindle-port: $(curl -s --max-time 5 -o /dev/null -w '%{http_code}' -A 'Kindle/3.0' http://127.0.0.1:8080/)"
