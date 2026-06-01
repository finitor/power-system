"""Decode battery CAN frames from a SocketCAN interface or candump log."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from offgrid_power.canbus import BatteryCanProtocol, CanFrame, candump_log_frames, decode_battery_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Decode battery CAN telemetry.")
    parser.add_argument("--interface", default="can0", help="SocketCAN interface to read")
    parser.add_argument("--seconds", type=float, default=3.0, help="Seconds to collect live frames")
    parser.add_argument("--log", type=Path, help="Decode a candump -L log instead of reading live CAN")
    parser.add_argument("--raw", action="store_true", help="Print raw latest frames after decoded summary")
    parser.add_argument(
        "--protocol",
        default=BatteryCanProtocol.PYLON.value,
        choices=[protocol.value for protocol in BatteryCanProtocol],
        help="Battery CAN decode profile",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.log is not None:
        frames = candump_log_frames(args.log.read_text(encoding="utf-8").splitlines())
    else:
        frames = _read_live_frames(args.interface, args.seconds)

    if not frames:
        print("No CAN frames received.")
        return 1

    snapshot = decode_battery_snapshot(frames, args.protocol)
    for line in snapshot.summary_lines():
        print(line)

    if args.raw:
        raw_frames = snapshot.raw_frames or {}
        print("Raw latest frames:")
        for frame_id in sorted(raw_frames):
            print(f"0x{frame_id:03X} {raw_frames[frame_id].hex(' ').upper()}")

    return 0


def _read_live_frames(interface: str, seconds: float) -> list[CanFrame]:
    try:
        import can
    except ImportError as exc:
        raise SystemExit("Install python-can with: python -m pip install '.[can]'") from exc

    frames: list[CanFrame] = []
    deadline = time.monotonic() + seconds
    with can.Bus(interface="socketcan", channel=interface) as bus:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            message = bus.recv(timeout=remaining)
            if message is None:
                break
            frames.append(
                CanFrame(
                    arbitration_id=message.arbitration_id,
                    data=bytes(message.data),
                    timestamp=message.timestamp,
                    is_extended_id=message.is_extended_id,
                )
            )
    return frames


if __name__ == "__main__":
    raise SystemExit(main())
