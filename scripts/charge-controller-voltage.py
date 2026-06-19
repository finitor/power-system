#!/usr/bin/env python3
"""Set one scalar charge voltage for a charge controller via the supervisor API.

The API hides controller-specific staging details:

- controller 0 / Classic: absorb, equalize, and max temp-comp are set to the
  requested voltage; float is set 0.1 V lower because the Classic requires it.
- controller 1 / EPEver: boost/absorb, float, and equalize are set to the
  requested voltage; boost recovery is set 1.0 V lower.

Examples:

    scripts/charge-controller-voltage.py 0 56.3
    scripts/charge-controller-voltage.py 1 56.4
    scripts/charge-controller-voltage.py 1 56.4 --dry-run
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
    parser.add_argument("voltage", type=float, help="scalar charge voltage in volts")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="supervisor base URL")
    parser.add_argument("--dry-run", action="store_true", help="show the API plan without writing")
    return parser.parse_args()


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
    response = api_json(
        args.api_url,
        "/api/v1/control/charge-controller/voltage",
        {
            "controller": args.controller,
            "voltage_v": args.voltage,
            "dry_run": args.dry_run,
        },
    )
    planned = response.get("planned") or {}
    action = "Planned" if response.get("dry_run") else "Wrote"
    device = response.get("device") or f"controller {args.controller}"
    print(f"{action} {device} scalar charge voltage {response.get('voltage_v'):.2f} V")
    for key in sorted(planned):
        print(f"  {key}: {planned[key]:.2f} V")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
