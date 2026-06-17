#!/bin/sh
# Set the MidNite Classic absorb voltage via the supervisor API (no restart, no
# adapter contention). Run on the Pi.
#
#   scripts/charge-classic-absorb.sh 55.0
#
# Voltage ordering on the Classic:
#   - EQUALIZE auto-clamps UP to absorb -- the controller enforces EQ >= absorb,
#     so raising absorb may raise EQ (observed: EQ followed absorb to 55.0).
#   - FLOAT is independent and is NOT touched here; keep it below absorb
#     yourself (set it with scripts/classic-charge-settings.py --float-voltage).
#   - The write is guarded against the BMS charge-voltage limit (CVL).
set -eu
[ "${1:-}" ] || { echo "usage: $0 <absorb_volts>" >&2; exit 2; }
curl -fsS -X POST http://127.0.0.1:8081/api/v1/control/classic/charge-settings \
    -H 'Content-Type: application/json' \
    -d "{\"absorb_voltage_v\": $1}"
echo
