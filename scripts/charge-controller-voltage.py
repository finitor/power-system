#!/usr/bin/env python3
"""Set or nudge one scalar charge voltage for a charge controller via the API.

The API hides controller-specific staging details:

- controller 0 / Classic: absorb, equalize, and max temp-comp are set to the
  requested voltage; float is set 0.1 V lower because the Classic requires it.
- controller 1 / EPEver: boost/absorb, float, and equalize are set to the
  requested voltage; boost recovery is set 1.0 V lower.

Give either an absolute voltage or a signed delta with --by. A delta is a
read-modify-write on the supervisor: it reads the controller's current scalar
setpoint, adds the delta, writes it back, and confirms the readback. Handy for
walking the voltage up or down a notch to watch the taper near the knee.

Examples:

    scripts/charge-controller-voltage.py 0 56.3
    scripts/charge-controller-voltage.py 1 56.4 --dry-run
    scripts/charge-controller-voltage.py 0 --by +0.1
    scripts/charge-controller-voltage.py 1 --by -0.1 --dry-run
"""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_URL = "http://127.0.0.1:8081"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("controller", type=int, help="charge controller number: 0=Classic, 1=EPEver")
    parser.add_argument(
        "voltage",
        type=float,
        nargs="?",
        help="absolute scalar charge voltage in volts (omit when using --by)",
    )
    parser.add_argument(
        "--by",
        dest="delta",
        type=float,
        help="signed delta in volts to add to the current scalar setpoint, e.g. +0.1 or -0.1",
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="supervisor base URL")
    parser.add_argument("--dry-run", action="store_true", help="show the API plan without writing")
    args = parser.parse_args()
    if (args.voltage is None) == (args.delta is None):
        parser.error("give exactly one of: a voltage argument or --by DELTA")
    return args


def api_json(base_url: str, path: str, payload: dict) -> dict:
    url = base_url.rstrip("/") + path
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach supervisor API at {url}: {exc}") from exc


def main() -> int:
    args = parse_args()
    request: dict = {"controller": args.controller, "dry_run": args.dry_run}
    if args.delta is not None:
        request["delta_v"] = args.delta
    else:
        request["voltage_v"] = args.voltage
    response = api_json(
        args.api_url,
        "/api/v1/control/charge-controller/voltage",
        request,
    )
    planned = response.get("planned") or {}
    device = response.get("device") or f"controller {args.controller}"
    verb = "Planned" if response.get("dry_run") else ("Nudged" if args.delta is not None else "Wrote")
    previous = response.get("previous_voltage_v")
    target = response.get("voltage_v")
    if previous is not None:
        summary = f"{verb} {device} scalar charge voltage {previous:.2f} -> {target:.2f} V"
    else:
        summary = f"{verb} {device} scalar charge voltage {target:.2f} V"
    confirmed = response.get("confirmed")
    if confirmed is True:
        summary += " (confirmed)"
    elif confirmed is False:
        summary += " (UNCONFIRMED — readback did not match)"
    print(summary)
    for key in sorted(planned):
        print(f"  {key}: {planned[key]:.2f} V")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
