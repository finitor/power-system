#!/bin/sh
# Escalation watchdog: reboot the Pi when the supervisor goes fully blind.
#
# This covers a failure that NOTHING else does. /healthz is liveness only (it
# returns "ok" as long as the process can build a snapshot), and Restart=on-failure
# handles a *crashed* supervisor. Neither catches the 2026-06-30 case: the process
# stayed up but every external transport died at once -- onboard eth (Classic over
# Modbus-TCP), RS485 (EPEver), Magnum serial, and USB-CAN (battery) all dropped in
# a ~90s window when a 5V brownout de-enumerated the USB bus. The supervisor ran
# blind for ~2.5h until a human pulled power.
#
# A process restart can't fix a de-enumerated USB bus -- only re-enumeration does,
# which means a reboot. So: when MULTIPLE independent transports are down at once
# (a system/bus wedge, not one dead sensor) and stay down past a sustained window,
# reboot. A persistent cooldown stops a recurring brownout from boot-looping the Pi.
# RebootWatchdogSec (config/systemd/system-watchdog.conf) forces the reboot through
# even if it hangs unmounting the wedged USB device.
#
# Signal: /api/v1/health "checks" -- per-device {ok,error,offline,disabled}. We
# count classic/epever/magnum/battery (ambient is onboard I2C, excluded -- it kept
# working last night). "disabled" = intentionally off, counts as fine.
#
# SAFETY: the reboot is gated behind SUPERVISOR_WATCHDOG_ARMED=1. Unset/0 = dry-run:
# it does everything (detect, log, notify) EXCEPT reboot, logging "would reboot" so
# the behaviour can be burned in for a few days before it is allowed to act.
#
# Run as root from offgrid-supervisor-watchdog.timer. Config via /etc/offgrid-power.env:
#   SUPERVISOR_WATCHDOG_ARMED   1 to enable the actual reboot (default: dry-run)
#   HC_SUPERVISOR_WATCHDOG_URL  Healthchecks.io ping URL for escalation reporting
#                               (optional; unset = log only). /fail on escalation,
#                               base URL on recovery. See docs/runbooks/healthchecks-escalation.md
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin

HEALTH_URL="${SUPERVISOR_WATCHDOG_HEALTH_URL:-http://127.0.0.1:8081/api/v1/health}"
QUORUM=3                  # this many of 4 real devices down = bus/system wedge
BLIND_AFTER_S=600         # must stay wedged this long before we act (~5 checks)
REBOOT_COOLDOWN_S=1800    # refuse to reboot again within this window (boot-loop guard)
ARMED="${SUPERVISOR_WATCHDOG_ARMED:-0}"
FIRSTBAD=/run/offgrid-supervisor-watchdog.first-bad        # volatile: degraded-since (clears on boot)
LASTBOOT=/var/lib/offgrid/supervisor-watchdog.last-reboot  # persistent: survives the reboot it triggers
PROBE=/var/lib/offgrid/watchdog-reboot-probe               # persistent: pre-reboot state, read back after the reboot

log() { echo "supervisor-watchdog: $*"; }

# Relay an escalation event to Healthchecks.io, if configured. $1 = ok|fail, $2 = body.
# A "fail" ping marks the check down (-> email); an "ok" ping marks it up (recovery).
# No-op when HC_SUPERVISOR_WATCHDOG_URL is unset, so this is inert until provisioned.
notify() {
    [ -n "${HC_SUPERVISOR_WATCHDOG_URL:-}" ] || return 0
    _url="${HC_SUPERVISOR_WATCHDOG_URL}"
    [ "$1" = "fail" ] && _url="${_url}/fail"
    curl -fsS -m 10 --retry 3 --data-raw "$2" "${_url}" >/dev/null 2>&1 \
        || log "healthcheck ping ($1) failed to send (network down?)"
}

# Prints a one-line "down=… status=…" summary to stdout; exit 0 = healthy enough,
# exit 1 = >=QUORUM real transports down (or the supervisor is not serving).
assess() {
    _body="$(curl -fsS -m 10 "${HEALTH_URL}" 2>/dev/null)" || {
        echo "down=UNREACHABLE status=not-serving"
        return 1
    }
    printf '%s' "${_body}" | QUORUM="${QUORUM}" python3 -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("down=UNPARSEABLE status=?"); sys.exit(1)
real = ("classic", "epever", "magnum", "battery")  # ambient (onboard) excluded
checks = d.get("checks", {})
down = [n for n in real if checks.get(n, {}).get("status") not in ("ok", "disabled")]
print("down=%s status=%s" % (",".join(down) or "none", d.get("status")))
sys.exit(1 if len(down) >= int(os.environ["QUORUM"]) else 0)
'
}

# Soft-reboot recovery probe. If we wrote a marker just before an armed reboot and
# that marker predates the current boot, the reboot has since happened -- so record
# whether it actually cleared the wedge. This answers the open question: does a soft
# reboot power-cycle a USB-firmware-wedged adapter, or come back still dead? (A
# physical unplug recovered it; unbind/rebind and USBDEVFS_RESET did not.) Logged to
# the now-persistent journal; HC ping if configured. Inert until an armed reboot fires.
if [ -f "${PROBE}" ]; then
    _pepoch="$(sed -n 1p "${PROBE}" 2>/dev/null || echo 0)"
    _pbefore="$(sed -n 2p "${PROBE}" 2>/dev/null || echo '?')"
    if [ "${_pepoch}" -lt "$(awk '/btime/{print $2}' /proc/stat)" ] 2>/dev/null; then
        _after="$(assess || true)"
        _result="soft-reboot recovery probe: armed reboot fired; before=[${_pbefore}] after=[${_after}]"
        log "${_result}"
        notify ok "${_result}"
        rm -f "${PROBE}"
    fi
fi

# `if summary=$(assess)` captures stdout AND the verdict without tripping set -e.
if summary="$(assess)"; then
    if [ -f "${FIRSTBAD}" ]; then
        rm -f "${FIRSTBAD}"
        log "recovered: ${summary}"
        notify ok "supervisor recovered: ${summary}"
    fi
    exit 0
fi

now="$(date +%s)"
[ -f "${FIRSTBAD}" ] || echo "${now}" > "${FIRSTBAD}"
blind="$(( now - $(cat "${FIRSTBAD}") ))"

if [ "${blind}" -lt "${BLIND_AFTER_S}" ]; then
    log "degraded ${blind}s (act at ${BLIND_AFTER_S}s): ${summary}"
    exit 0
fi

# Past the sustained threshold. In armed mode, honour the boot-loop cooldown.
if [ "${ARMED}" = "1" ] && [ -f "${LASTBOOT}" ]; then
    last="$(cat "${LASTBOOT}" 2>/dev/null || echo 0)"
    if [ "$(( now - last ))" -lt "${REBOOT_COOLDOWN_S}" ]; then
        msg="supervisor blind ${blind}s but rebooted $(( now - last ))s ago; refusing reboot (cooldown) -- MANUAL INTERVENTION NEEDED. ${summary}"
        log "${msg}"
        notify fail "${msg}"
        exit 0
    fi
fi

if [ "${ARMED}" = "1" ]; then
    msg="supervisor blind ${blind}s, >=${QUORUM} transports down -- REBOOTING to re-enumerate I/O. ${summary}"
    log "${msg}"
    notify fail "${msg}"
    mkdir -p /var/lib/offgrid
    echo "${now}" > "${LASTBOOT}"
    printf '%s\n%s\n' "${now}" "${summary}" > "${PROBE}"   # read back by the recovery probe after the reboot
    sync
    exec systemctl reboot
fi

# Dry-run: detect + log + notify, but never reboot. One log line per cycle while
# blind is the burn-in signal; Healthchecks dedups the fail ping to one email/episode.
msg="DRY-RUN: supervisor blind ${blind}s, >=${QUORUM} transports down -- would reboot now (set SUPERVISOR_WATCHDOG_ARMED=1 to arm). ${summary}"
log "${msg}"
notify fail "${msg}"
exit 0
