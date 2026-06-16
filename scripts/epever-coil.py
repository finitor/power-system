#!/usr/bin/env python3
"""Guarded EPEver coil control: charge on/off (0x0000) and clear accumulated
generation statistics (0x000E).

The supervisor owns /dev/epever-rs485, so this refuses to run while
offgrid-supervisor is active (two processes on one serial port corrupt the
bus). Stop it first, run this, then restart it:

    sudo systemctl stop offgrid-supervisor
    power-system/.venv/bin/python scripts/epever-coil.py status
    power-system/.venv/bin/python scripts/epever-coil.py charge off
    power-system/.venv/bin/python scripts/epever-coil.py charge on
    power-system/.venv/bin/python scripts/epever-coil.py clear-energy --confirm
    sudo systemctl start offgrid-supervisor

charge on/off is the true 0 A hard stop the current taper can't give.
clear-energy is DESTRUCTIVE: it zeroes the controller's accumulated
generation/energy counters and cannot be undone.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.epever import EpeverClient  # noqa: E402


def supervisor_active() -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "offgrid-supervisor"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() == "active"
    except Exception:  # noqa: BLE001 - if we can't tell, don't block
        return False


def show_status(client: EpeverClient) -> None:
    telemetry, _ = client.read()
    enabled = client.read_charge_enabled()
    print(f"charge coil (0x0000): {'ON' if enabled else 'OFF'}")
    print(f"charging status     : {telemetry.charging_status}")
    print(f"battery out         : {telemetry.battery_current_a:.2f} A / {telemetry.battery_power_w} W")
    print(f"generated today     : {telemetry.generated_today_kwh} kWh (provisional scale)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default="/dev/epever-rs485")
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="run even if the supervisor is active (risks bus contention)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="read charge coil + energy")
    charge = sub.add_parser("charge", help="enable/disable charging (coil 0x0000)")
    charge.add_argument("state", choices=["on", "off"])
    clear = sub.add_parser("clear-energy", help="DESTRUCTIVE: zero accumulated generation stats (coil 0x000E)")
    clear.add_argument("--confirm", action="store_true", help="required; this is irreversible")
    args = parser.parse_args()

    if supervisor_active() and not args.force:
        print("Refusing: offgrid-supervisor is active and owns the adapter.", file=sys.stderr)
        print("Stop it first (sudo systemctl stop offgrid-supervisor) or pass --force.", file=sys.stderr)
        return 1

    client = EpeverClient(device=args.device, unit=args.unit)

    if args.command == "status":
        show_status(client)
        return 0

    if args.command == "charge":
        want = args.state == "on"
        state = client.set_charging(want)
        print(f"charge coil (0x0000) now: {'ON' if state else 'OFF'}")
        return 0

    if args.command == "clear-energy":
        if not args.confirm:
            print("Refusing: clear-energy is irreversible. Re-run with --confirm.", file=sys.stderr)
            return 2
        before, _ = client.read()
        print(f"generated today before: {before.generated_today_kwh} kWh")
        client.clear_generation_statistics()
        after, _ = client.read()
        print(f"generated today after : {after.generated_today_kwh} kWh")
        print("cleared.")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
