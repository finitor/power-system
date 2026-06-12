"""Command-line HTTP display server for supervisor metrics."""

from __future__ import annotations

import argparse

from offgrid_power.cli.supervisor_display import add_supervisor_arguments, build_supervisor
from offgrid_power.web_display import run_display_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve live power-system metrics over HTTP.")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    add_supervisor_arguments(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    supervisor = build_supervisor(args)
    try:
        run_display_server(
            supervisor,
            host=args.host,
            port=args.port,
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
