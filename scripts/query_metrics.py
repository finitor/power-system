#!/usr/bin/env python3
"""Ad-hoc queries against the local telemetry SQLite store.

Run on the Pi (or any host with access to /srv/telemetry/data/metrics.sqlite):

    python3 scripts/query_metrics.py peak-power
    python3 scripts/query_metrics.py daily-summary
    python3 scripts/query_metrics.py daily-summary --date 2026-06-14
    python3 scripts/query_metrics.py now
    python3 scripts/query_metrics.py charge-history
    python3 scripts/query_metrics.py charge-history --date 2026-06-14
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = "/srv/telemetry/data/metrics.sqlite"


def open_db(path: str = DB_PATH) -> sqlite3.Connection:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Database not found: {path}")
    return sqlite3.connect(f"file:{path}?immutable=1", uri=True)


def day_bounds(d: date) -> tuple[str, str]:
    """Return ISO prefix bounds for a local-date range query."""
    return str(d), str(d + timedelta(days=1))


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_peak_power(args: argparse.Namespace) -> None:
    """Highest charging power recorded on a given day."""
    d = args.date
    lo, hi = day_bounds(d)
    conn = open_db(args.db)
    row = conn.execute(
        """
        SELECT captured_at, value, unit
        FROM samples
        WHERE source = 'classic.0'
          AND metric = 'battery_power'
          AND captured_at >= ? AND captured_at < ?
        ORDER BY value DESC
        LIMIT 1
        """,
        (lo, hi),
    ).fetchone()
    if row is None:
        print(f"No charging power data for {d}.")
        return
    ts, value, unit = row
    local_time = ts[11:16]  # HH:MM
    print(f"Peak charging power on {d}: {value:.0f} {unit} at {local_time}")


def cmd_daily_summary(args: argparse.Namespace) -> None:
    """End-of-day (or latest) daily totals from the Classic."""
    d = args.date
    lo, hi = day_bounds(d)
    conn = open_db(args.db)

    def latest(metric: str):
        row = conn.execute(
            """
            SELECT value, unit FROM samples
            WHERE source = 'classic.0' AND metric = ?
              AND captured_at >= ? AND captured_at < ?
            ORDER BY captured_at DESC LIMIT 1
            """,
            (metric, lo, hi),
        ).fetchone()
        return row if row else (None, None)

    def peak(metric: str):
        row = conn.execute(
            """
            SELECT value, unit, captured_at FROM samples
            WHERE source = 'classic.0' AND metric = ?
              AND captured_at >= ? AND captured_at < ?
            ORDER BY value DESC LIMIT 1
            """,
            (metric, lo, hi),
        ).fetchone()
        return row if row else (None, None, None)

    energy_kwh, _ = latest("daily_energy")
    amp_hours, _ = latest("daily_amp_hours")
    peak_w, _, peak_ts = peak("battery_power")
    peak_time = peak_ts[11:16] if peak_ts else "?"
    peak_v, _, _ = peak("pv_voltage")
    voc, _ = latest("last_voc")

    print(f"Daily summary for {d}:")
    print(f"  Energy:       {energy_kwh or '?'} kWh   ({amp_hours or '?'} Ah)")
    print(f"  Peak charge:  {peak_w or '?'} W  at {peak_time}")
    print(f"  Peak PV:      {peak_v or '?'} V")
    print(f"  Last VOC:     {voc or '?'} V")


def cmd_now(args: argparse.Namespace) -> None:
    """Most recent readings from every source."""
    conn = open_db(args.db)
    sources = [
        ("classic.0", ["battery_voltage", "battery_current", "battery_power",
                        "pv_voltage", "pv_current", "charge_stage", "daily_energy"]),
        ("battery",   ["voltage", "current", "soc", "temperature"]),
        ("battery.can", ["soc", "voltage", "current"]),
    ]
    for source, metrics in sources:
        print(f"\n[{source}]")
        for metric in metrics:
            row = conn.execute(
                """
                SELECT captured_at, value, text, unit FROM samples
                WHERE source = ? AND metric = ?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (source, metric),
            ).fetchone()
            if row is None:
                continue
            ts, value, text, unit = row
            age_note = f"  @ {ts[11:16]}"
            display = text if value is None else f"{value} {unit or ''}".strip()
            print(f"  {metric:<22} {display}{age_note}")


def cmd_charge_history(args: argparse.Namespace) -> None:
    """Charge stage transitions over a day."""
    d = args.date
    lo, hi = day_bounds(d)
    conn = open_db(args.db)
    rows = conn.execute(
        """
        SELECT captured_at, text, value
        FROM samples
        WHERE source = 'classic.0' AND metric = 'charge_stage'
          AND captured_at >= ? AND captured_at < ?
        ORDER BY captured_at
        """,
        (lo, hi),
    ).fetchall()
    if not rows:
        print(f"No charge stage data for {d}.")
        return

    print(f"Charge stage transitions on {d}:")
    last_stage = None
    for ts, text, _ in rows:
        if text != last_stage:
            print(f"  {ts[11:16]}  {text}")
            last_stage = text


def cmd_shell(args: argparse.Namespace) -> None:
    """Open an interactive Python REPL with conn pre-loaded."""
    import code
    conn = open_db(args.db)
    conn.row_factory = sqlite3.Row
    banner = (
        f"metrics shell — {args.db}\n"
        "  conn   sqlite3.Connection (row_factory=Row)\n"
        "  rows = conn.execute('SELECT ...').fetchall()\n"
        "  dict(rows[0])   # column names\n"
    )
    code.interact(banner=banner, local={"conn": conn, "sqlite3": sqlite3})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD, got: {s!r}")


def main() -> None:
    today = date.today()

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DB_PATH, help="Path to metrics.sqlite")

    sub = parser.add_subparsers(dest="command", required=True)

    p_peak = sub.add_parser("peak-power", help="Highest charging power on a day")
    p_peak.add_argument("--date", type=parse_date, default=today)
    p_peak.set_defaults(func=cmd_peak_power)

    p_summary = sub.add_parser("daily-summary", help="Daily energy totals")
    p_summary.add_argument("--date", type=parse_date, default=today)
    p_summary.set_defaults(func=cmd_daily_summary)

    p_now = sub.add_parser("now", help="Latest reading from every source")
    p_now.set_defaults(func=cmd_now)

    p_hist = sub.add_parser("charge-history", help="Charge stage transitions over a day")
    p_hist.add_argument("--date", type=parse_date, default=today)
    p_hist.set_defaults(func=cmd_charge_history)

    p_shell = sub.add_parser("shell", help="Interactive Python REPL with conn pre-loaded")
    p_shell.set_defaults(func=cmd_shell)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
