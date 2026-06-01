"""Survey unknown CAN traffic on one or more SocketCAN bitrates."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time

from offgrid_power.canbus import (
    BatteryCanProtocol,
    CanFrame,
    configure_socketcan_interface,
    decode_battery_snapshot,
)


@dataclass(frozen=True)
class CanTrafficSummary:
    frame_count: int
    unique_ids: tuple[int, ...]
    standard_count: int
    extended_count: int
    first_timestamp: float | None
    last_timestamp: float | None
    top_ids: tuple[tuple[int, int], ...]
    top_pgns: tuple[tuple[int, int], ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Survey CAN traffic at one or more bitrates and save candump-style logs."
    )
    parser.add_argument("--interface", default="can0", help="SocketCAN interface to survey")
    parser.add_argument(
        "--bitrates",
        default="500000,250000",
        help="Comma-separated bitrate list to try, for example 500000,250000",
    )
    parser.add_argument("--seconds", type=float, default=10.0, help="Seconds to collect at each bitrate")
    parser.add_argument("--label", default="can-survey", help="Label used in saved log filenames")
    parser.add_argument(
        "--protocol",
        default=BatteryCanProtocol.PYLON.value,
        choices=[protocol.value for protocol in BatteryCanProtocol],
        help="Battery CAN decode profile used for the summary hint",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/can-experiments"),
        help="Directory for candump-style logs",
    )
    parser.add_argument(
        "--active",
        action="store_true",
        help="Disable listen-only mode. Leave unset for passive traffic surveys.",
    )
    parser.add_argument("--no-save", action="store_true", help="Do not save candump-style logs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bitrates = parse_bitrates(args.bitrates)
    if not bitrates:
        raise SystemExit("No valid bitrates supplied.")

    overall_ok = False
    for bitrate in bitrates:
        print(f"Configuring {args.interface} at {bitrate} bit/s ({'active' if args.active else 'listen-only'})")
        configured = configure_socketcan_interface(
            args.interface,
            bitrate=bitrate,
            listen_only=not args.active,
        )
        if not configured:
            print(f"Interface {args.interface} is not present.")
            return 1

        frames = read_live_frames(args.interface, args.seconds)
        summary = summarize_frames(frames)
        print_summary(bitrate, summary)

        if frames and not args.no_save:
            path = save_candump_log(args.output_dir, args.label, bitrate, args.interface, frames)
            print(f"Saved {path}")

        if frames:
            overall_ok = True
            print_protocol_hint(frames, args.protocol)
        print()

    return 0 if overall_ok else 1


def parse_bitrates(text: str) -> list[int]:
    bitrates: list[int] = []
    for part in text.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        bitrates.append(int(stripped))
    return bitrates


def read_live_frames(interface: str, seconds: float) -> list[CanFrame]:
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


def summarize_frames(frames: list[CanFrame]) -> CanTrafficSummary:
    ids = Counter(frame.arbitration_id for frame in frames)
    pgns = Counter(pgn_from_arbitration_id(frame.arbitration_id) for frame in frames if frame.is_extended_id)
    timestamps = [frame.timestamp for frame in frames if frame.timestamp is not None]
    return CanTrafficSummary(
        frame_count=len(frames),
        unique_ids=tuple(sorted(ids)),
        standard_count=sum(1 for frame in frames if not frame.is_extended_id),
        extended_count=sum(1 for frame in frames if frame.is_extended_id),
        first_timestamp=min(timestamps) if timestamps else None,
        last_timestamp=max(timestamps) if timestamps else None,
        top_ids=tuple(ids.most_common(12)),
        top_pgns=tuple(pgns.most_common(12)),
    )


def pgn_from_arbitration_id(arbitration_id: int) -> int:
    pdu_format = (arbitration_id >> 16) & 0xFF
    pdu_specific = (arbitration_id >> 8) & 0xFF
    pgn = (arbitration_id >> 8) & 0x3FFFF
    if pdu_format < 240:
        pgn &= ~0xFF
    else:
        pgn = (pgn & ~0xFF) | pdu_specific
    return pgn


def print_summary(bitrate: int, summary: CanTrafficSummary) -> None:
    print(f"{bitrate} bit/s: {summary.frame_count} frames, {len(summary.unique_ids)} unique IDs")
    if summary.frame_count == 0:
        print("No frames received at this bitrate.")
        return

    print(f"Frame IDs: {summary.standard_count} standard, {summary.extended_count} extended")
    if summary.first_timestamp is not None and summary.last_timestamp is not None:
        print(f"Capture span: {summary.last_timestamp - summary.first_timestamp:.3f}s")

    ids = ", ".join(f"0x{frame_id:X} ({count})" for frame_id, count in summary.top_ids)
    print(f"Top IDs: {ids}")

    if summary.top_pgns:
        pgns = ", ".join(f"0x{pgn:05X} ({count})" for pgn, count in summary.top_pgns)
        print(f"Top extended PGNs: {pgns}")


def print_protocol_hint(frames: list[CanFrame], protocol: str) -> None:
    snapshot = decode_battery_snapshot(frames, protocol)
    lines = [
        line
        for line in snapshot.summary_lines()
        if line.startswith(("0x351 ", "0x355 ", "0x356 ", "0x359 ", "0x35C ", "0x35E "))
    ]
    if not lines:
        print(f"{protocol} battery frames: not detected in this capture")
        return

    print(f"{protocol} battery frames detected:")
    for line in lines:
        print(f"  {line}")


def save_candump_log(
    output_dir: Path,
    label: str,
    bitrate: int,
    interface: str,
    frames: list[CanFrame],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(character if character.isalnum() or character in "-_" else "-" for character in label)
    path = output_dir / f"{timestamp}-{safe_label}-{bitrate}.log"
    path.write_text(
        "".join(candump_line(interface, frame) for frame in frames),
        encoding="utf-8",
    )
    return path


def candump_line(interface: str, frame: CanFrame) -> str:
    timestamp = frame.timestamp if frame.timestamp is not None else time.time()
    return f"({timestamp:.6f}) {interface} {frame.arbitration_id:X}#{frame.data.hex().upper()}\n"


if __name__ == "__main__":
    raise SystemExit(main())
