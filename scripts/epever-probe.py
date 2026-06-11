#!/usr/bin/env python3
"""Read-only Modbus RTU probe for the EPEver TEP10425 COM port.

Bench tool: confirms comms and dumps the standard EPEver (Tracer/B-series)
register blocks so we can verify the TEP10425 follows the published map.
Strictly read-only.

Wiring (COM port RJ45 -> RS485 adapter): pin 6 = A+, pin 3 = B-, pin 8 = GND.
Pin 1 carries +5VDC -- leave it disconnected.

    power-system/.venv/bin/python scripts/epever-probe.py [--device /dev/epever-rs485]

Stop the supervisor first before opening the EPEver adapter directly
(sudo systemctl stop offgrid-supervisor), and restart it afterwards.
"""

from __future__ import annotations

import argparse
import sys

from pymodbus.client import ModbusSerialClient

# Standard EPEver register blocks (B-series/Tracer protocol).
INPUT_BLOCKS = [
    ("rated data", 0x3000, 9),
    ("real-time PV/battery", 0x3100, 8),
    ("temperatures", 0x3110, 2),
    ("battery SOC", 0x311A, 1),
    ("status", 0x3200, 2),
]
HOLDING_BLOCKS = [
    ("battery settings", 0x9000, 15),
]

BAUDS = [115200, 9600]


def try_connect(device: str, baud: int, unit: int):
    client = ModbusSerialClient(
        port=device, baudrate=baud, parity="N", stopbits=1, bytesize=8, timeout=1.5
    )
    if not client.connect():
        return None
    response = client.read_input_registers(address=0x3100, count=1, device_id=unit)
    if response.isError():
        client.close()
        return None
    return client


def dump_block(client, kind: str, label: str, address: int, count: int, unit: int) -> None:
    read = client.read_input_registers if kind == "input" else client.read_holding_registers
    response = read(address=address, count=count, device_id=unit)
    if response.isError():
        print(f"  {label} (0x{address:04X} x{count}): ERROR {response}")
        return
    regs = response.registers
    pairs = "  ".join(f"0x{address + i:04X}={v}" for i, v in enumerate(regs))
    print(f"  {label}:")
    print(f"    {pairs}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/epever-rs485")
    parser.add_argument("--unit", type=int, default=1, help="Modbus device id (EPEver default 1)")
    parser.add_argument("--baud", type=int, default=0, help="Force a baud rate instead of auto-try")
    args = parser.parse_args()

    bauds = [args.baud] if args.baud else BAUDS
    client = None
    for baud in bauds:
        client = try_connect(args.device, baud, args.unit)
        if client is not None:
            print(f"connected: {args.device} @ {baud} baud, device id {args.unit}")
            break
        print(f"no response @ {baud} baud")
    if client is None:
        print("FAILED: no Modbus response. Check wiring (A/B swap?), power, device id.")
        return 1

    try:
        for label, address, count in INPUT_BLOCKS:
            dump_block(client, "input", label, address, count, args.unit)
        for label, address, count in HOLDING_BLOCKS:
            dump_block(client, "holding", label, address, count, args.unit)
        print()
        print("hint: voltages/currents are typically value/100; check 0x3104 (battery V)")
        print("      and 0x311A (SOC %) against the LCD to confirm scaling.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
