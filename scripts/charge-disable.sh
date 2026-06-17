#!/bin/sh
# EMERGENCY: stop charging. Run on the Pi.
#
# The live allocator reconciles the EPEver coil every cycle, so it would undo a
# plain coil-off. This stops the supervisor first (freezing the allocator and
# freeing the serial bus), then commands the controllers off directly:
#   - EPEver: charge coil 0x0000 OFF -- the reliable hard stop (no BMS needed).
#   - Classic: current limit -> 0 A (best-effort; needs a BMS CCL read, may warn).
#
# While disabled the supervisor is DOWN (no telemetry/logging). The BMS hard
# limits remain the real protection regardless. Re-enable with charge-enable.sh.
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$DIR/.venv/bin/python"

echo "stopping supervisor (so the allocator can't re-enable)..."
sudo systemctl stop offgrid-supervisor
sleep 1

echo "EPEver charge coil OFF..."
"$PY" "$DIR/scripts/epever-coil.py" --direct charge off || echo "  WARNING: EPEver coil-off failed"

echo "Classic current limit -> 0 A..."
"$PY" "$DIR/scripts/classic-charge-settings.py" --no-battery-can-auto-up \
    --battery-can-seconds 6 --battery-current-limit 0 --no-persist \
    || echo "  WARNING: Classic 0 A write failed (BMS read?); pull the Classic PV breaker for a guaranteed stop"

echo "charging disabled. Run scripts/charge-enable.sh to resume."
