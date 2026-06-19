#!/bin/sh
# Compatibility wrapper: set the MidNite Classic scalar charge voltage via the
# supervisor API (no restart, no adapter contention). Run on the Pi.
#
#   scripts/charge-classic-absorb.sh 55.0
#
# This now uses the scalar charge-voltage API:
#   - absorb, equalize, and max temp-comp are set to the requested voltage
#   - float is set 0.1 V lower because the Classic requires float < absorb
#   - the write is guarded against the BMS charge-voltage limit (CVL)
set -eu
[ "${1:-}" ] || { echo "usage: $0 <absorb_volts>" >&2; exit 2; }
curl -fsS -X POST http://127.0.0.1:8081/api/v1/control/charge-controller/voltage \
    -H 'Content-Type: application/json' \
    -d "{\"controller\": 0, \"voltage_v\": $1}"
echo
