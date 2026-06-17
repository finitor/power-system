#!/bin/sh
# Resume charging after charge-disable.sh. Run on the Pi.
#
# Re-enables the EPEver coil and a baseline Classic limit while the supervisor is
# still stopped, then restarts the supervisor. If the allocator is live it takes
# over from there (re-apportions the Classic limit, holds the EPEver coil on).
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$DIR/.venv/bin/python"

echo "EPEver charge coil ON..."
"$PY" "$DIR/scripts/epever-coil.py" --direct charge on || echo "  WARNING: EPEver coil-on failed"

echo "Classic current limit -> 80 A (baseline; the allocator re-tunes if live)..."
"$PY" "$DIR/scripts/classic-charge-settings.py" --no-battery-can-auto-up \
    --battery-can-seconds 6 --battery-current-limit 80 --no-persist \
    || echo "  WARNING: Classic limit restore failed; set it manually"

echo "starting supervisor..."
sudo systemctl start offgrid-supervisor
sleep 2
systemctl is-active offgrid-supervisor
echo "charging re-enabled."
