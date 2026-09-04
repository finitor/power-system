#!/usr/bin/env python3
"""Read the Magnum MS4448PAE Low Battery Cut Out setting without writing.

Run on the Raspberry Pi:

    .venv/bin/python scripts/magnum-lbco.py

The read is passive. The configured LBCO is intentionally not added to the
supervisor's regular telemetry schema; this command is for one-off checks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "software" / "pi-controller" / "src"))

from offgrid_power.magnum import MagnumClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default="/dev/magnum-rs485")
    parser.add_argument("--max-cycles", type=int, default=20)
    args = parser.parse_args()

    lbco_v = MagnumClient(args.device, max_cycles=args.max_cycles).read_lbco_v()
    if lbco_v is None:
        print("No repeated valid Magnum LBCO value was received.", file=sys.stderr)
        return 1
    print(f"Magnum LBCO: {lbco_v:.1f} V")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
