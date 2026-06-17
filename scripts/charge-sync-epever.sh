#!/bin/sh
# Copy the MidNite Classic's charge voltages to the EPEver via the supervisor
# API, with an optional offset (volts) added to all of them. Run on the Pi.
#
#   scripts/charge-sync-epever.sh          # match the Classic exactly
#   scripts/charge-sync-epever.sh 0.3      # EPEver 0.3 V above the Classic
#   scripts/charge-sync-epever.sh -- -0.2  # EPEver 0.2 V below the Classic
#
# Maps Classic absorb->EPEver boost, float->float, equalize->equalize (+offset).
# The EPEver requires equalize >= boost, so equalize is auto-clamped up to the
# target boost. Guarded against the BMS charge-voltage limit (CVL).
set -eu
OFFSET="${1:-0}"
curl -fsS -X POST http://127.0.0.1:8081/api/v1/control/epever/sync-from-classic \
    -H 'Content-Type: application/json' \
    -d "{\"voltage_offset_v\": $OFFSET}"
echo
