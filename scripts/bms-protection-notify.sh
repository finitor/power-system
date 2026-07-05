#!/bin/sh
# Report an active BMS *protection* (hardware cutoff engaged) to a dedicated,
# high-urgency Healthchecks.io check -- separate from the routine
# supervisor-degraded channel.
#
# Rationale: a BMS protection flag means the battery's own hardware backstop has
# tripped (cell over/under voltage, over/under temperature, over-current). In a
# normally supervised system this must NEVER happen -- the charge allocator and
# controller regulation keep the pack far inside these limits -- so its presence
# is extreme degradation: the last line of defense actually engaged. It warrants
# its own alert channel so it isn't diluted among "one controller offline"-class
# WARNINGs on the shared degraded check. Point HC_BMS_PROTECTION_URL at a check
# with immediate/push notification configured.
#
# Fires ONLY on conditions prefixed "BMS protection:" (ERROR-severity hardware
# trips). BMS *alarms* (pre-trip WARNINGs) stay on the degraded channel.
#
# Event-driven and inert by default: pings only on transitions, and only when
# HC_BMS_PROTECTION_URL is set. Runs as the offgrid service user (only reads
# localhost health and posts to Healthchecks).
#
# Config via /etc/offgrid-power.env:
#   HC_BMS_PROTECTION_URL   Healthchecks ping URL (base; /fail appended on alert).
#                           Unset = log only. See docs/runbooks/healthchecks-escalation.md
#   BMS_PROTECTION_AFTER_S  Debounce before alerting (default 0 = alert on first
#                           detection; protection trips are CRC-valid and urgent).
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin

HEALTH_URL="${BMS_PROTECTION_HEALTH_URL:-http://127.0.0.1:8081/api/v1/health}"
PROTECTION_AFTER_S="${BMS_PROTECTION_AFTER_S:-0}"   # debounce (default: alert at once)
STATE_DIR=/var/lib/offgrid
SINCE="${STATE_DIR}/bms-protection.since"      # protected-since timestamp (debounce)
ALERTED="${STATE_DIR}/bms-protection.alerted"  # an outstanding /fail awaiting recovery

log() { echo "bms-protection-notify: $*"; }

[ -d "${STATE_DIR}" ] || { log "state dir ${STATE_DIR} missing; supervisor not yet started"; exit 0; }

notify() {  # $1 = ok|fail, $2 = body
    [ -n "${HC_BMS_PROTECTION_URL:-}" ] || return 0
    _url="${HC_BMS_PROTECTION_URL}"
    [ "$1" = "fail" ] && _url="${_url}/fail"
    curl -fsS -m 10 --retry 3 --data-raw "$2" "${_url}" >/dev/null 2>&1 \
        || log "healthcheck ping ($1) failed to send (network down?)"
}

# Prints "BMS protection active: …" to stdout; exit 0 = no protection, exit 1 =
# at least one active BMS protection. A health endpoint that is unreachable or
# unparseable is NOT treated as a protection trip -- that failure mode belongs to
# the degraded/watchdog channels, and this check must not cry wolf on it.
assess() {
    _body="$(curl -fsS -m 10 "${HEALTH_URL}" 2>/dev/null)" || {
        echo "health endpoint unreachable (not a protection signal)"
        return 0
    }
    printf '%s' "${_body}" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("health endpoint unparseable (not a protection signal)"); sys.exit(0)
prot = [c for c in (d.get("conditions") or []) if str(c).startswith("BMS protection:")]
if prot:
    print("BMS protection active: " + "; ".join(prot)); sys.exit(1)
print("no BMS protection"); sys.exit(0)
'
}

if summary="$(assess)"; then
    rm -f "${SINCE}"
    if [ -f "${ALERTED}" ]; then
        rm -f "${ALERTED}"
        log "cleared: ${summary}"
        notify ok "BMS protection cleared: ${summary}"
    fi
    exit 0
fi

# A protection is active.
now="$(date +%s)"
[ -f "${SINCE}" ] || echo "${now}" > "${SINCE}"

if [ -f "${ALERTED}" ]; then
    exit 0                       # already alerted; HC stays down until it clears
fi

active="$(( now - $(cat "${SINCE}") ))"
if [ "${active}" -lt "${PROTECTION_AFTER_S}" ]; then
    log "protection active ${active}s (alert at ${PROTECTION_AFTER_S}s): ${summary}"
    exit 0
fi

log "ALERTING: BMS protection active ${active}s: ${summary}"
: > "${ALERTED}"
notify fail "BMS protection active -- battery hardware cutoff engaged: ${summary}"
exit 0
