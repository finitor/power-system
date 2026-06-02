#!/usr/bin/env python3
"""Watch battery CAN traffic and print decoded snapshots plus raw changes."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import CanFrame, decode_pylon_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Pylon-style battery CAN traffic.")
    parser.add_argument("--interface", default="can0", help="SocketCAN interface to read")
    parser.add_argument("--snapshot-seconds", type=float, default=30.0, help="Decoded snapshot interval")
    parser.add_argument("--change-ids", default="", help="Comma-separated hex IDs to log changes for")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        import can
    except ImportError as exc:
        raise SystemExit("Install python-can with: python -m pip install '.[can]'") from exc

    watched_ids = parse_ids(args.change_ids) or {
        0x008,
        0x351,
        0x355,
        0x356,
        0x359,
        0x35C,
        0x35F,
        0x370,
        0x371,
        0x372,
        0x373,
        0x374,
        0x375,
        0x376,
        0x377,
        0x379,
    }
    latest: dict[int, CanFrame] = {}
    last_data: dict[int, bytes] = {}
    next_snapshot = 0.0

    with can.Bus(interface="socketcan", channel=args.interface) as bus:
        while True:
            message = bus.recv(timeout=1.0)
            now = time.monotonic()
            if message is not None:
                frame = CanFrame(
                    arbitration_id=message.arbitration_id,
                    data=bytes(message.data),
                    timestamp=message.timestamp,
                )
                latest[frame.arbitration_id] = frame
                if frame.arbitration_id in watched_ids and last_data.get(frame.arbitration_id) != frame.data:
                    last_data[frame.arbitration_id] = frame.data
                    print(
                        f"{timestamp()} CHANGE 0x{frame.arbitration_id:03X} "
                        f"{frame.data.hex(' ').upper()}",
                        flush=True,
                    )

            if latest and now >= next_snapshot:
                print_snapshot(latest.values())
                next_snapshot = now + args.snapshot_seconds


def parse_ids(value: str) -> set[int]:
    ids: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        ids.add(int(part, 16))
    return ids


def timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def print_snapshot(frames: Iterable[CanFrame]) -> None:
    print(f"===== {timestamp()} SNAPSHOT =====", flush=True)
    snapshot = decode_pylon_snapshot(frames)
    for line in snapshot.summary_lines():
        print(line, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
