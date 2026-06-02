#!/usr/bin/env python3
"""Guarded MidNite Classic charge-setting writer."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import BatteryCanClient, BatteryCanProtocol, ensure_socketcan_interface_up, socketcan_interfaces
from offgrid_power.charge_policy import (
    ClassicChargeTargets,
    planned_classic_settings,
    validate_classic_targets_against_bms,
)
from offgrid_power.classic import ClassicClient
from offgrid_power.config import load_config


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description="Write Classic charge settings after checking BMS CVL/CCL.")
    parser.add_argument("--classic-host", default=config.classic.host)
    parser.add_argument("--classic-port", type=int, default=config.classic.port)
    parser.add_argument("--classic-device-id", type=int, default=config.classic.device_id)
    parser.add_argument("--classic-timeout", type=float, default=config.classic.timeout_s)
    parser.add_argument("--battery-can-interface", default="can0")
    parser.add_argument("--battery-can-bitrate", type=int, default=500000)
    parser.add_argument("--battery-can-seconds", type=float, default=1.5)
    parser.add_argument(
        "--battery-can-protocol",
        default=config.battery_can.protocol,
        choices=[protocol.value for protocol in BatteryCanProtocol],
    )
    parser.add_argument("--no-battery-can-auto-up", action="store_true")
    parser.add_argument("--absorb-voltage", type=float)
    parser.add_argument("--float-voltage", type=float)
    parser.add_argument("--equalize-voltage", type=float)
    parser.add_argument("--absorb-time", type=int)
    parser.add_argument("--max-temp-comp-voltage", type=float)
    parser.add_argument("--battery-current-limit", type=float)
    parser.add_argument("--force", action="store_true", help="Write even if planned settings exceed BMS CVL/CCL")
    parser.add_argument("--dry-run", action="store_true", help="Check and print planned settings without writing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = ClassicChargeTargets(
        battery_current_limit_a=args.battery_current_limit,
        absorb_voltage_v=args.absorb_voltage,
        float_voltage_v=args.float_voltage,
        equalize_voltage_v=args.equalize_voltage,
        absorb_time_s=args.absorb_time,
        max_temp_comp_voltage_v=args.max_temp_comp_voltage,
    )
    if targets == ClassicChargeTargets():
        print("No settings requested; nothing to write.", file=sys.stderr)
        return 2

    classic = ClassicClient(
        host=args.classic_host,
        port=args.classic_port,
        device_id=args.classic_device_id,
        timeout=args.classic_timeout,
    )
    _, current_settings = classic.read()
    charge_limits = read_bms_charge_limits(args)
    planned = planned_classic_settings(current_settings, targets)
    violations = validate_classic_targets_against_bms(planned, charge_limits)

    print(
        "BMS limits: "
        f"CVL {charge_limits.charge_voltage_limit_v:.1f}V, "
        f"CCL {charge_limits.charge_current_limit_a:.1f}A"
    )
    print(
        "Planned Classic settings: "
        f"Limit {planned.battery_current_limit_a:.1f}A, "
        f"Absorb {planned.absorb_voltage_v:.1f}V for {planned.absorb_time_s}s, "
        f"Float {planned.float_voltage_v:.1f}V, "
        f"EQ {planned.equalize_voltage_v:.1f}V, "
        f"Max temp-comp {planned.max_temp_comp_voltage_v:.1f}V"
    )
    if violations:
        for violation in violations:
            print(f"Refusing: {violation}", file=sys.stderr)
        if not args.force:
            print("Use --force to override this guard deliberately.", file=sys.stderr)
            return 1
        print("Forced write despite BMS limit violation.", file=sys.stderr)

    if args.dry_run:
        print("Dry run only; no settings written.")
        return 0

    readback = classic.write_charge_settings(
        battery_current_limit_a=targets.battery_current_limit_a,
        absorb_voltage_v=targets.absorb_voltage_v,
        float_voltage_v=targets.float_voltage_v,
        equalize_voltage_v=targets.equalize_voltage_v,
        absorb_time_s=targets.absorb_time_s,
        max_temp_comp_voltage_v=targets.max_temp_comp_voltage_v,
    )
    print(
        "Readback: "
        f"Limit {readback.battery_current_limit_a:.1f}A, "
        f"Absorb {readback.absorb_voltage_v:.1f}V for {readback.absorb_time_s}s, "
        f"Float {readback.float_voltage_v:.1f}V, "
        f"EQ {readback.equalize_voltage_v:.1f}V, "
        f"Max temp-comp {readback.max_temp_comp_voltage_v:.1f}V"
    )
    return 0


def read_bms_charge_limits(args: argparse.Namespace):
    if not args.no_battery_can_auto_up:
        ensure_socketcan_interface_up(
            args.battery_can_interface,
            bitrate=args.battery_can_bitrate,
            listen_only=True,
        )
    if args.battery_can_interface not in socketcan_interfaces():
        raise RuntimeError(f"CAN interface {args.battery_can_interface} is not present")

    snapshot = BatteryCanClient(
        interface=args.battery_can_interface,
        receive_seconds=args.battery_can_seconds,
        protocol=args.battery_can_protocol,
    ).read()
    if snapshot.charge_limits is None:
        raise RuntimeError("Battery CAN snapshot did not include charge limits")
    return snapshot.charge_limits


if __name__ == "__main__":
    raise SystemExit(main())
