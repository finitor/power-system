#!/bin/sh
# Auto-recover the gs_usb CAN adapter when the bus goes silent.
#
# The SH-C31G's URB pipeline can wedge after USB disturbances: the adapter
# enumerates and can0 stays UP, but no frames arrive until the device is
# unbound/rebound. Run from offgrid-can-watchdog.timer as root.
#
# Note: the Cubix BMS also legitimately stops transmitting at idle, so a
# silent bus is not proof of a wedge. The reset is harmless in that case;
# the cooldown keeps us from churning USB while the BMS is quiet.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin

IFACE=can0
SYS="/sys/class/net/${IFACE}"
SAMPLE_S=10
COOLDOWN_S=600
STAMP=/run/offgrid-can-watchdog.last-reset

log() { echo "can-watchdog: $*"; }

if [ ! -d "${SYS}" ]; then
    log "${IFACE} absent; nothing to reset (replug handled by udev)"
    exit 0
fi

rx1="$(cat "${SYS}/statistics/rx_packets")"
sleep "${SAMPLE_S}"
rx2="$(cat "${SYS}/statistics/rx_packets")"
if [ "${rx1}" != "${rx2}" ]; then
    exit 0
fi

now="$(date +%s)"
if [ -f "${STAMP}" ]; then
    last="$(cat "${STAMP}")"
    if [ $((now - last)) -lt "${COOLDOWN_S}" ]; then
        log "bus silent but last reset $((now - last))s ago; in cooldown"
        exit 0
    fi
fi

usbdev="$(basename "$(readlink -f "${SYS}/device")" | cut -d: -f1)"
log "no frames in ${SAMPLE_S}s; resetting USB ${usbdev} and ${IFACE}"
echo "${now}" > "${STAMP}"

echo "${usbdev}" > /sys/bus/usb/drivers/usb/unbind || true
sleep 2
echo "${usbdev}" > /sys/bus/usb/drivers/usb/bind
sleep 2
ip link set "${IFACE}" down 2>/dev/null || true
ip link set "${IFACE}" type can bitrate 500000 listen-only on
ip link set "${IFACE}" up
log "reset complete"
