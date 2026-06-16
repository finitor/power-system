#!/usr/bin/env python3
"""Request Magnum charge-setting changes through the supervisor API.

The supervisor exposes this endpoint now, but the Magnum backend currently
returns "not implemented" until a safe remote-packet write primitive is
verified. This script exists so the operator-facing API shape is stable.
"""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_API_URL = "http://127.0.0.1:8081"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="supervisor base URL")
    parser.add_argument("--absorb-voltage", type=float)
    parser.add_argument("--float-voltage", type=float)
    parser.add_argument("--absorb-time-hr", type=float)
    parser.add_argument("--charger-amps-pct", type=int)
    parser.add_argument("--shore-amps", type=int)
    return parser.parse_args()


def api_json(base_url: str, path: str, payload: dict) -> dict:
    url = base_url.rstrip("/") + path
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(detail)
        except json.JSONDecodeError as json_exc:
            raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from json_exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach supervisor API at {url}: {exc}") from exc


def main() -> int:
    args = parse_args()
    payload = {
        "absorb_voltage_v": args.absorb_voltage,
        "float_voltage_v": args.float_voltage,
        "absorb_time_hr": args.absorb_time_hr,
        "charger_amps_pct": args.charger_amps_pct,
        "shore_amps": args.shore_amps,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    if not payload:
        print("No Magnum charge settings requested.")
        return 2

    response = api_json(args.api_url, "/api/v1/control/magnum/charge-settings", payload)
    if not response.get("ok"):
        print(f"Magnum charge-setting request refused: {response.get('error')}")
        if response.get("reason"):
            print(f"Reason: {response['reason']}")
        return 1
    print("Magnum charge settings updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
