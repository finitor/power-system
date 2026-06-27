#!/usr/bin/env python3
"""Read or write the EPEver battery rated capacity (register 0x9001).

    power-system/.venv/bin/python scripts/epever-battery-capacity.py get
    power-system/.venv/bin/python scripts/epever-battery-capacity.py set 200

Requires direct serial access; stop the supervisor first:

    sudo systemctl stop offgrid-supervisor
    power-system/.venv/bin/python scripts/epever-battery-capacity.py set 200
    sudo systemctl start offgrid-supervisor
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "software" / "pi-controller" / "src"))

from offgrid_power.epever import EpeverClient  # noqa: E402


def supervisor_active() -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "offgrid-supervisor"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() == "active"
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default="/dev/epever-rs485")
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="run even if the supervisor is active")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("get", help="read current battery capacity setting")
    set_cmd = sub.add_parser("set", help="write battery capacity in Ah")
    set_cmd.add_argument("capacity_ah", type=int)
    args = parser.parse_args()

    if supervisor_active() and not args.force:
        print("Refusing: offgrid-supervisor is active and owns the adapter.", file=sys.stderr)
        print("Stop it first (sudo systemctl stop offgrid-supervisor) or pass --force.", file=sys.stderr)
        return 1

    client = EpeverClient(device=args.device, unit=args.unit)

    if args.command == "get":
        _, settings = client.read()
        print(f"battery_capacity_ah : {settings.battery_capacity_ah}")
        print(f"battery_type        : {settings.battery_type} (code {settings.battery_type_code})")
        return 0

    if args.command == "set":
        _, before = client.read()
        print(f"before: {before.battery_capacity_ah} Ah")
        settings = client.write_battery_capacity(args.capacity_ah)
        print(f"after : {settings.battery_capacity_ah} Ah")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
