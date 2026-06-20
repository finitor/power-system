#!/usr/bin/env python3
"""Set or nudge the CCL budget fraction via the supervisor API.

The CCL budget fraction is the allocator knob that scales the BMS
charge-current limit down to a working charge budget (default 50%). It only
bites near the taper knee, where the BMS CCL has dropped below the baseline —
exactly where you want to experiment. It is an in-memory knob: it resets to the
configured default when the supervisor restarts.

This script speaks percent for convenience; the API speaks fractions. Give
either an absolute percent or a signed delta with --by. A delta is a
read-modify-write on the supervisor: read current fraction, add the delta,
write it back.

Examples:

    scripts/charge-budget.py 50            # set to 50%
    scripts/charge-budget.py --by +5       # nudge up 5 percentage points
    scripts/charge-budget.py --by -5 --dry-run
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
    parser.add_argument(
        "percent",
        type=float,
        nargs="?",
        help="absolute CCL budget as a percent, e.g. 50 (omit when using --by)",
    )
    parser.add_argument(
        "--by",
        dest="delta_pct",
        type=float,
        help="signed delta in percentage points to add to the current fraction, e.g. +5 or -5",
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="supervisor base URL")
    parser.add_argument("--dry-run", action="store_true", help="show the result without writing")
    args = parser.parse_args()
    if (args.percent is None) == (args.delta_pct is None):
        parser.error("give exactly one of: a percent argument or --by DELTA")
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
    request: dict = {"dry_run": args.dry_run}
    # The API is in fractions (0.0-1.0); the operator-facing CLI is in percent.
    if args.delta_pct is not None:
        request["delta"] = round(args.delta_pct / 100.0, 4)
    else:
        request["fraction"] = round(args.percent / 100.0, 4)
    response = api_json(args.api_url, "/api/v1/control/charge-budget/ccl-fraction", request)

    verb = "Planned" if response.get("dry_run") else ("Nudged" if args.delta_pct is not None else "Set")
    previous = response.get("previous_fraction")
    target = response.get("fraction")
    if previous is not None:
        print(f"{verb} CCL budget fraction {previous * 100:.0f}% -> {target * 100:.0f}%")
    else:
        print(f"{verb} CCL budget fraction {target * 100:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
