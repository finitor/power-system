"""Command-line terminal display for the Pi supervisor."""

from __future__ import annotations

import argparse
import time

from offgrid_power.classic import ClassicClient
from offgrid_power.config import load_config
from offgrid_power.supervisor import Supervisor
from offgrid_power.terminal_display import clear_screen, render_snapshot


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description="Display live power-system metrics.")
    parser.add_argument("--classic-host", default=config.classic.host)
    parser.add_argument("--classic-port", type=int, default=config.classic.port)
    parser.add_argument("--classic-device-id", type=int, default=config.classic.device_id)
    parser.add_argument("--classic-timeout", type=float, default=config.classic.timeout_s)
    parser.add_argument("--interval", type=float, default=config.display.refresh_seconds)
    parser.add_argument(
        "--no-clear",
        action="store_true",
        default=not config.display.clear_screen,
        help="Do not clear the terminal before each redraw",
    )
    parser.add_argument("--once", action="store_true", help="Render one snapshot and exit")
    return parser.parse_args()


def build_supervisor(args: argparse.Namespace) -> Supervisor:
    return Supervisor(
        classic=ClassicClient(
            host=args.classic_host,
            port=args.classic_port,
            device_id=args.classic_device_id,
            timeout=args.classic_timeout,
        )
    )


def main() -> int:
    args = parse_args()
    supervisor = build_supervisor(args)

    try:
        while True:
            snapshot = supervisor.read_snapshot()
            if not args.no_clear:
                clear_screen()
            print(render_snapshot(snapshot))
            if args.once:
                return 0 if snapshot.ok else 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

