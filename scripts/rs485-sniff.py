#!/usr/bin/env python3
"""Passive RS485/Modbus RTU sniffer.

Use with a second 2-wire USB-RS485 adapter connected in parallel to the bus.
The script only reads from the serial port; it does not transmit.

Example:

    power-system/.venv/bin/python scripts/rs485-sniff.py \
        --device /dev/ttyUSB1 --baud 115200 --outfile data/epever-sniff.log
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover - operator guidance
    print("pyserial is required; install the project venv dependencies first", file=sys.stderr)
    raise


FUNCTION_NAMES = {
    0x03: "read holding",
    0x04: "read input",
    0x06: "write single",
    0x10: "write multiple",
}


def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def describe_frame(frame: bytes) -> str:
    if len(frame) < 4:
        return "short"

    expected_crc = modbus_crc(frame[:-2])
    observed_crc = frame[-2] | (frame[-1] << 8)
    crc_text = "crc=ok" if expected_crc == observed_crc else f"crc=bad expected={expected_crc:04x}"

    unit = frame[0]
    function = frame[1]
    if function & 0x80 and len(frame) >= 5:
        return f"unit={unit} exception-fn=0x{function:02x} code={frame[2]} {crc_text}"

    name = FUNCTION_NAMES.get(function, f"fn=0x{function:02x}")
    if function in (0x03, 0x04, 0x06) and len(frame) == 8:
        address = (frame[2] << 8) | frame[3]
        value = (frame[4] << 8) | frame[5]
        return f"unit={unit} {name} addr=0x{address:04x} value/count={value} {crc_text}"

    if function == 0x10 and len(frame) >= 9:
        address = (frame[2] << 8) | frame[3]
        count = (frame[4] << 8) | frame[5]
        if len(frame) == 8:
            return f"unit={unit} {name} ack addr=0x{address:04x} count={count} {crc_text}"
        byte_count = frame[6]
        return (
            f"unit={unit} {name} addr=0x{address:04x} count={count} "
            f"bytes={byte_count} {crc_text}"
        )

    return f"unit={unit} {name} len={len(frame)} {crc_text}"


def emit(frame: bytes, outfile) -> None:
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
    hex_bytes = " ".join(f"{byte:02x}" for byte in frame)
    line = f"{timestamp}  {hex_bytes}  # {describe_frame(frame)}"
    print(line, flush=True)
    if outfile is not None:
        outfile.write(line + "\n")
        outfile.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, help="Sniffer serial device, e.g. /dev/ttyUSB1")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--gap-ms",
        type=float,
        default=20.0,
        help="Idle gap that ends a frame. USB buffering makes 20 ms more useful than pure RTU timing.",
    )
    parser.add_argument("--outfile", type=Path)
    args = parser.parse_args()

    outfile = args.outfile.open("a", encoding="utf-8") if args.outfile else None
    gap_seconds = args.gap_ms / 1000.0

    with serial.Serial(
        port=args.device,
        baudrate=args.baud,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=gap_seconds,
    ) as port:
        print(
            f"listening on {args.device} @ {args.baud} baud; press Ctrl-C to stop",
            flush=True,
        )
        frame = bytearray()
        try:
            while True:
                chunk = port.read(256)
                if chunk:
                    frame.extend(chunk)
                    continue
                if frame:
                    emit(bytes(frame), outfile)
                    frame.clear()
                time.sleep(0.001)
        except KeyboardInterrupt:
            if frame:
                emit(bytes(frame), outfile)
            print("stopped", flush=True)
        finally:
            if outfile is not None:
                outfile.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
