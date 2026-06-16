#!/usr/bin/env python3
"""Guarded EPEver coil control through the supervisor API.

By default, charge on/off is sent to the supervisor so the EPEver serial bus
stays owned by one process. Direct serial mode remains available for bench
recovery with --direct.

    power-system/.venv/bin/python scripts/epever-coil.py status
    power-system/.venv/bin/python scripts/epever-coil.py charge off
    power-system/.venv/bin/python scripts/epever-coil.py charge on
    power-system/.venv/bin/python scripts/epever-coil.py --direct clear-energy --confirm

Direct serial mode refuses to run while the supervisor is active unless
--force is passed.

    sudo systemctl stop offgrid-supervisor
    power-system/.venv/bin/python scripts/epever-coil.py --direct clear-energy --confirm
    sudo systemctl start offgrid-supervisor

charge on/off is the true 0 A hard stop the current taper can't give.
clear-energy is DESTRUCTIVE: it zeroes the controller's accumulated
generation/energy counters and cannot be undone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.epever import EpeverClient  # noqa: E402

DEFAULT_API_URL = "http://127.0.0.1:8081"


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


def api_json(base_url: str, path: str, payload: dict | None = None) -> dict:
    url = base_url.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach supervisor API at {url}: {exc}") from exc


def show_api_status(base_url: str) -> None:
    snapshot = api_json(base_url, "/api/v1/snapshot")
    epever = next((cc for cc in snapshot.get("solar") or [] if cc.get("id") == "epever.1"), None)
    if epever is None:
        raise RuntimeError("supervisor snapshot does not include epever.1")
    print("charge coil (0x0000): use 'charge on/off' to query by write readback")
    print(f"charging status     : {(epever.get('charge_stage') or {}).get('vendor') or (epever.get('charge_stage') or {}).get('canonical')}")
    print(f"battery out         : {epever.get('battery_current_a'):.2f} A / {epever.get('battery_power_w')} W")
    print(f"generated today     : {epever.get('daily_energy_kwh')} kWh")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default="/dev/epever-rs485")
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="supervisor base URL")
    parser.add_argument("--direct", action="store_true", help="open the EPEver serial device directly")
    parser.add_argument("--force", action="store_true", help="run even if the supervisor is active (risks bus contention)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show EPEver charging status + energy")
    charge = sub.add_parser("charge", help="enable/disable charging (coil 0x0000)")
    charge.add_argument("state", choices=["on", "off"])
    clear = sub.add_parser("clear-energy", help="DESTRUCTIVE: zero accumulated generation stats (coil 0x000E)")
    clear.add_argument("--confirm", action="store_true", help="required; this is irreversible")
    args = parser.parse_args()

    if args.command == "clear-energy" and not args.direct:
        print("Refusing: clear-energy is only available in --direct mode.", file=sys.stderr)
        return 2

    if args.direct and supervisor_active() and not args.force:
        print("Refusing: offgrid-supervisor is active and owns the adapter.", file=sys.stderr)
        print("Stop it first (sudo systemctl stop offgrid-supervisor) or pass --force.", file=sys.stderr)
        return 1

    client = EpeverClient(device=args.device, unit=args.unit) if args.direct else None

    if args.command == "status":
        if args.direct:
            show_status(client)
        else:
            show_api_status(args.api_url)
        return 0

    if args.command == "charge":
        want = args.state == "on"
        if args.direct:
            state = client.set_charging(want)
        else:
            state = api_json(args.api_url, "/api/v1/control/epever/charging", {"enabled": want})["enabled"]
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
