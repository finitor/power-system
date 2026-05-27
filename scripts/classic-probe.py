#!/usr/bin/env python3
"""Read-only MidNite Classic Modbus TCP probe."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterable

from pymodbus.client import ModbusTcpClient


DEFAULT_HOST = "192.168.0.10"
DEFAULT_PORT = 502
DEFAULT_DEVICE_ID = 10


CHARGE_STAGES = {
    0: "Resting",
    3: "Absorb",
    4: "BulkMppt",
    5: "Float",
    6: "FloatMppt",
    7: "Equalize",
    10: "HyperVoc",
    18: "EqMppt",
}

CLASSIC_STATES = {
    0: "Resting",
    1: "Waking / Starting",
    2: "Waking / Starting",
    3: "MPPT or regulating voltage",
    4: "MPPT or regulating voltage",
    6: "MPPT or regulating voltage",
}

MPPT_MODES = {
    0x0001: "PV U-Set",
    0x0003: "Dynamic",
    0x0005: "Wind Track",
    0x0009: "Legacy P&O",
    0x000B: "Solar",
    0x000D: "Hydro",
}

INFO_FLAGS = {
    0x00000001: "Classic over temperature",
    0x00000002: "EEPROM error",
    0x00000004: "Ethernet write lock",
    0x00000008: "Equalize in progress",
    0x00000100: "PV input lower than battery output",
    0x00000200: "Current limit reached",
    0x00000400: "HyperVoc",
    0x00002000: "Battery temperature sensor installed",
    0x00004000: "Aux1 on",
    0x00008000: "Aux2 on",
    0x00010000: "Ground fault",
    0x00020000: "Over current protect",
    0x00040000: "Arc fault",
    0x00080000: "Negative battery current",
    0x00200000: "Extra info available",
    0x00400000: "PV partial shade",
    0x00800000: "Watchdog reset",
    0x01000000: "Low battery voltage",
    0x02000000: "Stack jumper not installed",
    0x04000000: "Equalize done",
    0x08000000: "Temperature compensation shorted",
    0x10000000: "Unlock jumper not installed",
    0x20000000: "Extra jumper not installed",
    0x40000000: "PV input shorted",
}


@dataclass(frozen=True)
class RegisterBlock:
    start_register: int
    values: list[int]

    def get(self, register: int) -> int:
        index = register - self.start_register
        if index < 0 or index >= len(self.values):
            raise KeyError(register)
        return self.values[index]

    def as_dict(self) -> dict[int, int]:
        return {
            self.start_register + index: value
            for index, value in enumerate(self.values)
        }


def msb(value: int) -> int:
    return (value >> 8) & 0xFF


def lsb(value: int) -> int:
    return value & 0xFF


def u32(low_word: int, high_word: int) -> int:
    return (high_word << 16) + low_word


def flag_names(value: int) -> list[str]:
    return [name for bit, name in INFO_FLAGS.items() if value & bit]


def read_block(
    client: ModbusTcpClient,
    start_register: int,
    count: int,
    device_id: int,
) -> RegisterBlock:
    # MidNite's map lists Modbus register numbers. pymodbus expects packet
    # addresses, which are register number minus one for the Classic.
    response = client.read_holding_registers(
        address=start_register - 1,
        count=count,
        device_id=device_id,
    )
    if response.isError():
        raise RuntimeError(
            f"Modbus read failed for registers {start_register}.."
            f"{start_register + count - 1}: {response}"
        )
    return RegisterBlock(start_register, list(response.registers))


def print_raw(blocks: Iterable[RegisterBlock]) -> None:
    print("Raw registers:")
    for block in blocks:
        for register, value in block.as_dict().items():
            print(f"  {register}: {value}")


def print_live(block: RegisterBlock) -> None:
    combo_stage = block.get(4120)
    charge_stage = msb(combo_stage)
    classic_state = lsb(combo_stage)
    info_flags = u32(block.get(4130), block.get(4131))
    active_flags = flag_names(info_flags)

    print("Live telemetry:")
    print(f"  Battery voltage:       {block.get(4115) / 10:.1f} V")
    print(f"  PV input voltage:      {block.get(4116) / 10:.1f} V")
    print(f"  Battery current:       {block.get(4117) / 10:.1f} A")
    print(f"  Daily energy:          {block.get(4118) / 10:.1f} kWh")
    print(f"  Battery power:         {block.get(4119)} W")
    print(
        "  Charge stage:         "
        f"{charge_stage} ({CHARGE_STAGES.get(charge_stage, 'unknown')})"
    )
    print(
        "  Classic state:        "
        f"{classic_state} ({CLASSIC_STATES.get(classic_state, 'unknown')})"
    )
    print(f"  PV input current:      {block.get(4121) / 10:.1f} A")
    print(f"  Last measured Voc:     {block.get(4122) / 10:.1f} V")
    print(f"  Highest input voltage: {block.get(4123) / 10:.1f} V")
    print(f"  Daily amp-hours:       {block.get(4125)} Ah")
    print(f"  Lifetime energy:       {u32(block.get(4126), block.get(4127))} kWh")
    print(f"  Lifetime amp-hours:    {u32(block.get(4128), block.get(4129))} Ah")
    print(f"  Info flags:            0x{info_flags:08X}")
    if active_flags:
        print("  Active flags:")
        for name in active_flags:
            print(f"    - {name}")
    else:
        print("  Active flags:          none decoded")
    print(f"  Battery temp:          {block.get(4132) / 10:.1f} C")
    print(f"  FET temp:              {block.get(4133) / 10:.1f} C")
    print(f"  PCB temp:              {block.get(4134) / 10:.1f} C")


def print_settings(block: RegisterBlock) -> None:
    mppt_mode_raw = block.get(4164)
    mppt_enabled = bool(mppt_mode_raw & 0x0001)
    mppt_mode_on_value = mppt_mode_raw | 0x0001

    print("Charge settings:")
    print(f"  Battery current limit: {block.get(4148) / 10:.1f} A")
    print(f"  Absorb voltage:        {block.get(4149) / 10:.1f} V")
    print(f"  Float voltage:         {block.get(4150) / 10:.1f} V")
    print(f"  Equalize voltage:      {block.get(4151) / 10:.1f} V")
    print(f"  Sliding current limit: {block.get(4152)} A")
    print(f"  Absorb time setpoint:  {block.get(4154)} s")
    print(f"  Max temp-comp voltage: {block.get(4155) / 10:.1f} V")
    print(f"  Min temp-comp voltage: {block.get(4156) / 10:.1f} V")
    print(f"  Temp comp value:       -{block.get(4157) / 10:.1f} mV/C/cell")
    print(
        "  MPPT mode:             "
        f"0x{mppt_mode_raw:04X} "
        f"({MPPT_MODES.get(mppt_mode_on_value, 'unknown')}, "
        f"{'enabled' if mppt_enabled else 'disabled'})"
    )
    print(f"  AUX function word:     0x{block.get(4165):04X}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read live MidNite Classic data over Modbus TCP."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Classic IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID)
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw register values after decoded output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = ModbusTcpClient(args.host, port=args.port, timeout=3)

    if not client.connect():
        print(f"Could not connect to {args.host}:{args.port}")
        return 1

    try:
        live = read_block(client, start_register=4115, count=20, device_id=args.device_id)
        settings = read_block(
            client,
            start_register=4148,
            count=18,
            device_id=args.device_id,
        )

        print(f"Classic Modbus TCP probe: {args.host}:{args.port}")
        print(f"Device id: {args.device_id}")
        print()
        print_live(live)
        print()
        print_settings(settings)
        if args.raw:
            print()
            print_raw([live, settings])
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
