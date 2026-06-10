#!/usr/bin/env python3
"""Print the procurement view of hardware/inventory.csv.

The inventory file is the single source of truth for hardware; this is a
filter over it, so there is no second list to drift. Items with status
"needed" or "ordered" are shown; pass --all to include every status.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

INVENTORY = Path(__file__).resolve().parents[1] / "hardware" / "inventory.csv"
PROCUREMENT_STATUSES = ("needed", "ordered")
STATUS_ORDER = ("needed", "ordered", "on-hand", "installed", "deferred", "retired")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="show every status, not just needed/ordered")
    args = parser.parse_args()

    with INVENTORY.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    statuses = STATUS_ORDER if args.all else PROCUREMENT_STATUSES
    shown = [row for row in rows if row["status"] in statuses]
    shown.sort(key=lambda row: (STATUS_ORDER.index(row["status"]), row["category"], row["item"]))

    width_item = max((len(f"{r['item']} ({r['manufacturer']} {r['model']})") for r in shown), default=10)
    current = None
    for row in shown:
        if row["status"] != current:
            current = row["status"]
            print(f"\n== {current} ==")
        label = f"{row['item']} ({row['manufacturer']} {row['model']})"
        print(f"  {label:<{width_item}}  x{row['qty']:<3} {row['purpose']}")
    print(f"\n{len(shown)} items shown of {len(rows)} in inventory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
