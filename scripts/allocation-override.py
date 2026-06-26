#!/usr/bin/env python3
"""Pause the charge allocator or set per-controller current-limit ceilings via the supervisor API.

Controllers are addressed by integer index (0 = classic, 1 = epever).

All state is in-memory: it resets to allocator-controlled mode on supervisor restart.

Examples:

    scripts/allocation-override.py status
    scripts/allocation-override.py pause
    scripts/allocation-override.py resume
    scripts/allocation-override.py limit 0 0          # cap classic at 0 A
    scripts/allocation-override.py limit 0 --clear    # restore allocator control for classic
    scripts/allocation-override.py limit 1 30         # cap epever at 30 A
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_URL = "http://127.0.0.1:8081"


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        payload = json.loads(exc.read())
        print(f"Error {exc.code}: {payload.get('error', exc.reason)}", file=sys.stderr)
        sys.exit(1)
    except URLError as exc:
        print(f"Cannot reach supervisor: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def _get(url: str) -> dict:
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        payload = json.loads(exc.read())
        print(f"Error {exc.code}: {payload.get('error', exc.reason)}", file=sys.stderr)
        sys.exit(1)
    except URLError as exc:
        print(f"Cannot reach supervisor: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    result = _get(f"{args.api_url}/api/v1/control/allocation/status")
    print(f"paused: {result['paused']}")
    limits = result.get("manual_limits_a", {})
    for idx_str, val in sorted(limits.items()):
        label = f"controller {idx_str}"
        print(f"  {label}: {val if val is not None else '(allocator-controlled)'}")


def cmd_pause(args: argparse.Namespace) -> None:
    result = _post(f"{args.api_url}/api/v1/control/allocation/pause", {"paused": True})
    print(f"paused: {result['previous_paused']} -> {result['paused']}")


def cmd_resume(args: argparse.Namespace) -> None:
    result = _post(f"{args.api_url}/api/v1/control/allocation/pause", {"paused": False})
    print(f"paused: {result['previous_paused']} -> {result['paused']}")


def cmd_limit(args: argparse.Namespace) -> None:
    limit_a = None if args.clear else args.limit_a
    result = _post(
        f"{args.api_url}/api/v1/control/allocation/manual-limit",
        {"controller": args.controller, "limit_a": limit_a},
    )
    prev = result["previous_limit_a"]
    cur = result["limit_a"]
    prev_str = f"{prev} A" if prev is not None else "allocator-controlled"
    cur_str = f"{cur} A" if cur is not None else "allocator-controlled"
    print(f"controller {args.controller}: {prev_str} -> {cur_str}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pause the charge allocator or set per-controller current-limit ceilings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show current pause state and manual limits")
    sub.add_parser("pause", help="Pause allocator writes (evaluation continues)")
    sub.add_parser("resume", help="Resume allocator writes")

    p_limit = sub.add_parser("limit", help="Set or clear a per-controller current ceiling")
    p_limit.add_argument("controller", type=int, metavar="INDEX", help="Controller index (0=classic, 1=epever)")
    group = p_limit.add_mutually_exclusive_group(required=True)
    group.add_argument("limit_a", type=float, nargs="?", metavar="AMPS", help="Current ceiling in amps (>=0)")
    group.add_argument("--clear", action="store_true", help="Remove manual limit, restore allocator control")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "status":
        cmd_status(args)
    elif args.command == "pause":
        cmd_pause(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "limit":
        cmd_limit(args)


if __name__ == "__main__":
    main()
