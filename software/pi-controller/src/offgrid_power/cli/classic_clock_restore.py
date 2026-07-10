"""Restore the Pi clock from the Classic RTC when internet NTP is absent."""

from __future__ import annotations

import argparse
import sys

from offgrid_power.clock_restore import restore_system_clock
from offgrid_power.config import load_config


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classic-host", default=config.classic.host)
    parser.add_argument("--classic-port", type=int, default=config.classic.port)
    parser.add_argument("--classic-device-id", type=int, default=config.classic.device_id)
    parser.add_argument("--classic-timeout", type=float, default=config.classic.timeout_s)
    parser.add_argument("--timezone", default="America/Toronto")
    parser.add_argument("--ntp-wait-seconds", type=float, default=15.0)
    parser.add_argument("--classic-wait-seconds", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--ignore-ntp",
        action="store_true",
        help="Read/evaluate the Classic even if NTP is synchronized (intended for --dry-run diagnostics)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = restore_system_clock(
            host=args.classic_host,
            port=args.classic_port,
            device_id=args.classic_device_id,
            timeout=args.classic_timeout,
            timezone_name=args.timezone,
            ntp_wait_seconds=args.ntp_wait_seconds,
            classic_wait_seconds=args.classic_wait_seconds,
            dry_run=args.dry_run,
            ignore_ntp=args.ignore_ntp,
        )
    except Exception as exc:  # noqa: BLE001 - report a boot helper failure to journald.
        print(f"Classic clock restore failed: {exc}", file=sys.stderr)
        return 1

    fields = [f"action={result.action}", result.detail]
    if result.classic_time is not None:
        fields.append(f"classic={result.classic_time.isoformat()}")
    if result.system_time is not None:
        fields.append(f"system={result.system_time.isoformat()}")
    print("; ".join(fields))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
