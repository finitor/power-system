#!/bin/sh
# Report supervisor health degradation to Healthchecks.io.
#
# Complements supervisor-watchdog.sh: that one only fires on the rare total
# blackout (>=3 transports down -> reboot). This gives routine visibility into
# *any* degradation -- a single transport offline, a controller fault, a stale
# reading -- by watching /api/v1/health and pinging a Healthchecks check on the
# OK -> degraded edge (/fail, with detail) and the degraded -> OK edge (success).
#
# Event-driven and inert by default: pings only on transitions, and only when
# HC_SUPERVISOR_DEGRADED_URL is set. Debounced (DEGRADED_AFTER_S) so a single
# transient poll doesn't alert. Runs as the offgrid service user (no privilege
# needed -- it only reads localhost health and posts to Healthchecks).
#
# Config via /etc/offgrid-power.env:
#   HC_SUPERVISOR_DEGRADED_URL  Healthchecks ping URL (base; /fail appended on alert).
#                               Unset = log only. See docs/runbooks/healthchecks-escalation.md
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin

HEALTH_URL="${SUPERVISOR_DEGRADED_HEALTH_URL:-http://127.0.0.1:8081/api/v1/health}"
DEGRADED_AFTER_S=120     # degraded must persist this long before alerting (debounce)
STATE_DIR=/var/lib/offgrid
SINCE="${STATE_DIR}/degraded-notify.since"      # degraded-since timestamp (debounce)
ALERTED="${STATE_DIR}/degraded-notify.alerted"  # an outstanding /fail awaiting a recovery ping

log() { echo "degraded-notify: $*"; }

[ -d "${STATE_DIR}" ] || { log "state dir ${STATE_DIR} missing; supervisor not yet started"; exit 0; }

notify() {  # $1 = ok|fail, $2 = body
    [ -n "${HC_SUPERVISOR_DEGRADED_URL:-}" ] || return 0
    _url="${HC_SUPERVISOR_DEGRADED_URL}"
    [ "$1" = "fail" ] && _url="${_url}/fail"
    curl -fsS -m 10 --retry 3 --data-raw "$2" "${_url}" >/dev/null 2>&1 \
        || log "healthcheck ping ($1) failed to send (network down?)"
}

# Prints "status=… | detail" to stdout; exit 0 = OK, exit 1 = degraded (WARNING/ERROR).
assess() {
    _body="$(curl -fsS -m 10 "${HEALTH_URL}" 2>/dev/null)" || {
        echo "status=UNREACHABLE | supervisor not serving /api/v1/health"
        return 1
    }
    printf '%s' "${_body}" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("status=UNPARSEABLE"); sys.exit(1)
status = d.get("status")
bad = ["%s(%s)" % (k, v.get("status")) for k, v in d.get("checks", {}).items()
       if v.get("status") not in ("ok", "disabled")]
conds = d.get("conditions") or []
detail = "status=%s" % status
if bad:   detail += " | down: " + ", ".join(sorted(bad))
if conds: detail += " | conditions: " + "; ".join(conds)
print(detail)
sys.exit(0 if status == "OK" else 1)
'
}

if summary="$(assess)"; then
    rm -f "${SINCE}"
    if [ -f "${ALERTED}" ]; then
        rm -f "${ALERTED}"
        log "recovered: ${summary}"
        notify ok "supervisor health recovered: ${summary}"
    fi
    exit 0
fi

# Degraded.
now="$(date +%s)"
[ -f "${SINCE}" ] || echo "${now}" > "${SINCE}"

if [ -f "${ALERTED}" ]; then
    exit 0                       # already alerted; HC stays down until recovery
fi

degraded="$(( now - $(cat "${SINCE}") ))"
if [ "${degraded}" -lt "${DEGRADED_AFTER_S}" ]; then
    log "degraded ${degraded}s (alert at ${DEGRADED_AFTER_S}s): ${summary}"
    exit 0
fi

log "alerting: degraded ${degraded}s: ${summary}"
: > "${ALERTED}"
notify fail "supervisor health degraded: ${summary}"
exit 0
