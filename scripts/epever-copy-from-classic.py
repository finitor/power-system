#!/usr/bin/env python3
"""One-shot copy of charge parameters from the MidNite Classic to the EPEver.

The Classic is treated as the baseline. This aligns the EPEver's charge
voltages (and optionally the charge-current limit) to the Classic's, so both
controllers pursue the same staged-charging goal. It maps:

    Classic absorb_voltage   -> EPEver boost (absorption) voltage  (0x900B)
    Classic float_voltage    -> EPEver float voltage               (0x900C)
    Classic equalize_voltage -> EPEver equalize voltage            (0x900A)
    Classic current limit    -> EPEver max charging current        (0x9013)

The EPEver's protection thresholds (over-voltage disconnect/reconnect,
low-voltage disconnect/reconnect, discharge limit) have no Classic
counterpart and are left untouched.

Dry-run by default: prints the planned diff and writes nothing. Pass --write
to apply. Charge-voltage writes require EPEver Battery Type = User; the
writer enforces this and aborts otherwise.

Stop the supervisor before opening the EPEver adapter directly
(sudo systemctl stop offgrid-supervisor) and restart it afterwards.

    .venv/bin/python scripts/epever-copy-from-classic.py            # dry-run
    .venv/bin/python scripts/epever-copy-from-classic.py --write    # apply
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.classic import ClassicClient
from offgrid_power.config import load_config
from offgrid_power.epever import EpeverClient


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--classic-host", default=config.classic.host)
    parser.add_argument("--classic-port", type=int, default=config.classic.port)
    parser.add_argument("--classic-device-id", type=int, default=config.classic.device_id)
    parser.add_argument("--classic-timeout", type=float, default=config.classic.timeout_s)
    parser.add_argument("--epever-device", default=config.epever.device)
    parser.add_argument("--epever-baud", type=int, default=config.epever.baud)
    parser.add_argument("--epever-unit", type=int, default=config.epever.unit)
    parser.add_argument(
        "--no-current",
        action="store_true",
        help="Copy only the charge voltages; leave the EPEver current limit alone",
    )
    parser.add_argument("--write", action="store_true", help="Apply the changes (default is dry-run)")
    return parser.parse_args()


def _fmt(value: float) -> str:
    return f"{value:.2f}V"


def main() -> int:
    args = parse_args()

    classic_client = ClassicClient(
        host=args.classic_host,
        port=args.classic_port,
        device_id=args.classic_device_id,
        timeout=args.classic_timeout,
    )
    _, classic = classic_client.read()

    epever_client = EpeverClient(
        device=args.epever_device,
        baud=args.epever_baud,
        unit=args.epever_unit,
    )
    _, epever = epever_client.read()

    # (label, classic source value, current epever value, epever register)
    voltage_plan = [
        ("Boost (absorb)", classic.absorb_voltage_v, epever.boost_voltage_v, "boost_v"),
        ("Float", classic.float_voltage_v, epever.float_voltage_v, "float_v"),
        ("Equalize", classic.equalize_voltage_v, epever.equalize_voltage_v, "equalize_v"),
    ]

    print(f"Classic baseline @ {args.classic_host}  ->  EPEver @ {args.epever_device}")
    print(f"EPEver Battery Type: {epever.battery_type} (code {epever.battery_type_code})")
    print(f"EPEver charging-limit voltage (ceiling): {_fmt(epever.charging_limit_voltage_v)}")
    print()
    print(f"  {'Parameter':<16}{'Classic':>10}{'EPEver now':>14}{'->':>5}{'EPEver new':>14}")

    voltage_kwargs: dict[str, float] = {}
    blocked = False
    for label, source, current, kwarg in voltage_plan:
        change = "" if abs(source - current) < 0.01 else "  *"
        ceiling_hit = source > epever.charging_limit_voltage_v + 1e-9
        flag = "  !! exceeds charging-limit ceiling" if ceiling_hit else change
        print(f"  {label:<16}{_fmt(source):>10}{_fmt(current):>14}{'->':>5}{_fmt(source):>14}{flag}")
        if ceiling_hit:
            blocked = True
        elif abs(source - current) >= 0.01:
            voltage_kwargs[kwarg] = source

    current_change = None
    if not args.no_current:
        target_current = classic.battery_current_limit_a
        now_current = epever.max_charging_current_a
        now_str = _fmt(now_current).replace("V", "A") if now_current is not None else "n/a"
        if now_current is None or abs(target_current - now_current) >= 0.1:
            current_change = target_current
        print(f"  {'Max charge A':<16}{target_current:>9.1f}A{now_str:>14}{'->':>5}{target_current:>13.1f}A")

    print()
    if blocked:
        print("ABORT: a target voltage exceeds the EPEver charging-limit ceiling (0x9008).")
        print("Raise the EPEver charging-limit voltage first, or reconcile the Classic setting.")
        return 2

    if not voltage_kwargs and current_change is None:
        print("Already aligned; nothing to write.")
        return 0

    if not args.write:
        print("Dry-run: no changes written. Re-run with --write to apply.")
        return 0

    if voltage_kwargs:
        settings = epever_client.write_charge_voltages(**voltage_kwargs)
        print(
            "Wrote voltages -> "
            f"Equalize {_fmt(settings.equalize_voltage_v)}  "
            f"Boost {_fmt(settings.boost_voltage_v)}  "
            f"Float {_fmt(settings.float_voltage_v)}"
        )
    if current_change is not None:
        settings = epever_client.write_max_charging_current(current_change)
        print(f"Wrote max charging current -> {settings.max_charging_current_a:.1f}A")

    print("Done. Verified by read-back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
