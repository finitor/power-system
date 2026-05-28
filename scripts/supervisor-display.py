#!/usr/bin/env python3
"""Continuously display read-only supervisory metrics."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.classic import ClassicClient  # noqa: E402
from offgrid_power.supervisor import Supervisor  # noqa: E402
from offgrid_power.terminal_display import clear_screen, render_snapshot  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Display live power-system metrics.")
    parser.add_argument("--classic-host", default="192.168.0.10")
    parser.add_argument("--classic-port", type=int, default=502)
    parser.add_argument("--classic-device-id", type=int, default=10)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="Render one snapshot and exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    supervisor = Supervisor(
        classic=ClassicClient(
            host=args.classic_host,
            port=args.classic_port,
            device_id=args.classic_device_id,
        )
    )

    try:
        while True:
            snapshot = supervisor.read_snapshot()
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

