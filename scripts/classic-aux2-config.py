#!/usr/bin/env python3
"""Configure Classic AUX2 to "Active HIGH (input) turn off" (function 15).

Writes register 4165 to 0x4F01 so that >6 V on AUX2+ forces the Classic
to Resting, enabling hardware charge-disable via relay CH2 (GPIO 27).

Usage:
    python scripts/classic-aux2-config.py [--dry-run] [--no-persist]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.classic import (
    AUX_FUNCTION_WORD_CHARGE_DISABLE,
    AUX_FUNCTION_WORD_REGISTER,
    ClassicClient,
)
from offgrid_power.config import load_config


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--classic-host", default=config.classic.host)
    parser.add_argument("--classic-port", type=int, default=config.classic.port)
    parser.add_argument("--classic-device-id", type=int, default=config.classic.device_id)
    parser.add_argument("--classic-timeout", type=float, default=config.classic.timeout_s)
    parser.add_argument("--dry-run", action="store_true", help="Read and print current value; do not write")
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Change live setting only; do not force an EEPROM save",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    classic = ClassicClient(
        host=args.classic_host,
        port=args.classic_port,
        device_id=args.classic_device_id,
        timeout=args.classic_timeout,
    )

    _, current_settings = classic.read()
    current = current_settings.aux_function_word
    print(f"Current  AUX function word: 0x{current:04X} (reg {AUX_FUNCTION_WORD_REGISTER})")
    print(f"Target   AUX function word: 0x{AUX_FUNCTION_WORD_CHARGE_DISABLE:04X} (AUX2=fn15 Active-HIGH-off, AUX1 unchanged)")

    if current == AUX_FUNCTION_WORD_CHARGE_DISABLE:
        print("Already at target value — no write needed.")
        return 0

    if args.dry_run:
        print("Dry run — no write performed.")
        return 0

    readback = classic.write_aux_function_word(
        AUX_FUNCTION_WORD_CHARGE_DISABLE,
        persist=not args.no_persist,
    )
    print(f"Readback AUX function word: 0x{readback:04X}")
    if readback != AUX_FUNCTION_WORD_CHARGE_DISABLE:
        print("ERROR: readback does not match target value.", file=sys.stderr)
        return 1
    print("AUX2 reconfigured successfully.")
    if not args.no_persist:
        print("EEPROM saved — verify via Classic front panel after a power cycle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
