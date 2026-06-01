"""Command-line terminal display for the Pi supervisor."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from threading import Thread
import time

from offgrid_power.ambient import AmbientDhtClient, AmbientDs18b20Client
from offgrid_power.canbus import BatteryCanClient, socketcan_interfaces
from offgrid_power.classic import ClassicClient
from offgrid_power.config import load_config
from offgrid_power.supervisor import Supervisor
from offgrid_power.terminal_display import clear_screen, highlight_changed_digits, render_snapshot
from offgrid_power.web_display import HouseholdLoadTracker, SnapshotCache, run_display_server


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description="Display live power-system metrics.")
    add_supervisor_arguments(parser)
    parser.add_argument("--interval", type=float, default=config.display.refresh_seconds)
    parser.add_argument("--web-display", action="store_true", help="Serve the same supervisor snapshots over HTTP")
    parser.add_argument("--web-host", default="0.0.0.0", help="HTTP display bind address")
    parser.add_argument("--web-port", type=int, default=8080, help="HTTP display port")
    parser.add_argument(
        "--web-access-log-path",
        default="data/web-display-access.log",
        help="Append HTTP display access logs here; use an empty string to log to stdout",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        default=not config.display.clear_screen,
        help="Do not clear the terminal before each redraw",
    )
    parser.add_argument("--once", action="store_true", help="Render one snapshot and exit")
    return parser.parse_args()


def add_supervisor_arguments(parser: argparse.ArgumentParser) -> None:
    config = load_config()
    parser.add_argument("--classic-host", default=config.classic.host)
    parser.add_argument("--classic-port", type=int, default=config.classic.port)
    parser.add_argument("--classic-device-id", type=int, default=config.classic.device_id)
    parser.add_argument("--classic-timeout", type=float, default=config.classic.timeout_s)
    parser.add_argument("--no-classic", action="store_true", help="Disable MidNite Classic reads")
    parser.add_argument("--battery-can-interface", default="can0", help="SocketCAN battery interface")
    parser.add_argument("--battery-can-seconds", type=float, default=1.5, help="Seconds to collect battery CAN frames")
    parser.add_argument("--no-battery-can", action="store_true", help="Disable battery CAN reads")
    parser.add_argument(
        "--ambient-kind",
        choices=["dht11", "dht22", "ds18b20"],
        default=config.ambient.kind,
        help="Ambient sensor backend",
    )
    parser.add_argument("--ambient-gpio", type=int, default=config.ambient.gpio_pin)
    parser.add_argument(
        "--ds18b20-device-id",
        default=config.ambient.ds18b20_device_id,
        help="Specific DS18B20 id such as 28-000000000000; default reads the first probe",
    )
    parser.add_argument(
        "--no-ambient",
        action="store_true",
        default=not config.ambient.enabled,
        help="Disable ambient sensor reads",
    )
    parser.add_argument(
        "--ambient-log-path",
        default=config.ambient.log_path,
        help="Append ambient readings to this CSV file",
    )


def build_supervisor(args: argparse.Namespace) -> Supervisor:
    ambient = None
    if not args.no_ambient:
        if args.ambient_kind == "ds18b20":
            ambient = AmbientDs18b20Client(device_id=args.ds18b20_device_id)
        else:
            ambient = AmbientDhtClient(gpio_pin=args.ambient_gpio, sensor_type=args.ambient_kind)

    battery = None
    if not args.no_battery_can and args.battery_can_interface in socketcan_interfaces():
        battery = BatteryCanClient(interface=args.battery_can_interface, receive_seconds=args.battery_can_seconds)

    return Supervisor(
        classic=None
        if args.no_classic
        else ClassicClient(
            host=args.classic_host,
            port=args.classic_port,
            device_id=args.classic_device_id,
            timeout=args.classic_timeout,
        ),
        ambient=ambient,
        battery=battery,
    )


def append_ambient_log(log_path: str, snapshot) -> None:
    if not log_path or snapshot.ambient is None:
        return

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if needs_header:
            writer.writerow(["captured_at_utc", "temperature_c", "humidity_percent"])
        humidity = "" if snapshot.ambient.humidity_percent is None else f"{snapshot.ambient.humidity_percent:.2f}"
        writer.writerow(
            [
                snapshot.ambient.captured_at.isoformat(),
                f"{snapshot.ambient.temperature_c:.2f}",
                humidity,
            ]
        )


def main() -> int:
    args = parse_args()
    supervisor = build_supervisor(args)
    snapshot_cache = SnapshotCache()
    household_load_tracker = HouseholdLoadTracker()
    if args.web_display:
        start_web_display(args, supervisor, snapshot_cache)
    previous_render: str | None = None

    try:
        while True:
            snapshot = supervisor.read_snapshot()
            household_load = household_load_tracker.update(snapshot)
            snapshot_cache.set(snapshot, household_load)
            rendered = render_snapshot(snapshot, household_load=household_load)
            if not args.no_clear:
                clear_screen()
            print(highlight_changed_digits(previous_render, rendered))
            previous_render = rendered
            append_ambient_log(args.ambient_log_path, snapshot)
            if args.once:
                return 0 if snapshot.ok else 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


def start_web_display(args: argparse.Namespace, supervisor: Supervisor, snapshot_cache: SnapshotCache) -> None:
    thread = Thread(
        target=run_display_server,
        kwargs={
            "supervisor": supervisor,
            "host": args.web_host,
            "port": args.web_port,
            "snapshot_provider": snapshot_cache.get,
            "household_load_provider": snapshot_cache.get_household_load,
            "access_log_path": args.web_access_log_path or None,
        },
        daemon=True,
    )
    thread.start()


if __name__ == "__main__":
    raise SystemExit(main())
