"""Terminal display client for the supervisor HTTP API."""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from offgrid_power.api_terminal_display import render_api_snapshot, render_api_unavailable
from offgrid_power.terminal_display import clear_screen, highlight_changed_digits


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the supervisor API snapshot in a terminal.")
    parser.add_argument("--url", default="http://127.0.0.1:8080/api/v1/snapshot")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--no-clear", action="store_true", help="Do not clear the terminal before each redraw")
    parser.add_argument("--once", action="store_true", help="Render one API snapshot and exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    previous_render: str | None = None
    try:
        while True:
            started = time.monotonic()
            rendered = render_once(args.url, timeout=args.timeout)
            if not args.no_clear:
                clear_screen()
            print(highlight_changed_digits(previous_render, rendered), flush=True)
            previous_render = rendered
            if args.once:
                return 0
            remaining = args.interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    except KeyboardInterrupt:
        print()
        return 0


def render_once(url: str, timeout: float = 5.0) -> str:
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return render_api_snapshot(payload)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return render_api_unavailable(f"HTTP {exc.code}: {detail or exc.reason}")
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return render_api_unavailable(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
