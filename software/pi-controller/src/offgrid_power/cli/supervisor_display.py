"""Command-line terminal display for the Pi supervisor."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from threading import Thread
import time

from offgrid_power.ambient import AmbientDhtClient, AmbientDs18b20Client
from offgrid_power.canbus import (
    BatteryCanClient,
    BatteryCanProtocol,
    ensure_socketcan_interface_up,
    socketcan_interfaces,
)
from offgrid_power.charger_taper import (
    ChargerCurrentSettings,
    ChargerCurrentTaperController,
    ChargerTelemetry,
    append_decision_log,
)
from offgrid_power.classic import ClassicClient
from offgrid_power.epever import EpeverClient
from offgrid_power.magnum import MagnumClient
from offgrid_power.config import load_config
from offgrid_power.load import LoadTotalsTracker
from offgrid_power.metrics import MetricRecorder
from offgrid_power.supervisor import Supervisor
from offgrid_power.terminal_display import clear_screen, highlight_changed_digits, render_snapshot
from offgrid_power.weather import WeatherConfig, WeatherService
from offgrid_power.load import LoadSampleBuffer, LoadTracker
from offgrid_power.web_display import SnapshotCache, run_display_server


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description="Display live power-system metrics.")
    add_supervisor_arguments(parser)
    parser.add_argument("--interval", type=float, default=config.display.refresh_seconds)
    parser.add_argument(
        "--no-device-readers",
        action="store_true",
        help="Read devices synchronously in the main loop instead of per-device actor threads",
    )
    parser.add_argument("--battery-capacity-ah", type=float, default=config.display.battery_capacity_ah)
    parser.add_argument("--web-display", action="store_true", help="Serve the same supervisor snapshots over HTTP")
    parser.add_argument("--web-host", default="0.0.0.0", help="HTTP display bind address")
    parser.add_argument("--web-port", type=int, default=8080, help="HTTP display port")
    parser.add_argument(
        "--weather",
        action="store_true",
        default=_env_bool("WEATHER_ENABLED", False),
        help="Enable the Kindle weather page using Open-Meteo",
    )
    parser.add_argument("--weather-latitude", type=float, default=_env_float("WEATHER_LATITUDE", 0.0))
    parser.add_argument("--weather-longitude", type=float, default=_env_float("WEATHER_LONGITUDE", 0.0))
    parser.add_argument("--weather-label", default=os.getenv("WEATHER_LABEL", "Cabin"))
    parser.add_argument(
        "--weather-refresh-minutes",
        type=float,
        default=_env_float("WEATHER_REFRESH_MINUTES", 30.0),
    )
    parser.add_argument(
        "--weather-cache-path",
        default=os.getenv("WEATHER_CACHE_PATH", "/srv/telemetry/data/weather-cache.json"),
    )
    parser.add_argument(
        "--web-access-log-path",
        default="data/web-display-access.log",
        help="Append HTTP display access logs here; use an empty string to log to stdout",
    )
    parser.add_argument(
        "--load-sample-log-path",
        default="data/load-samples.csv",
        help="Append rolling load samples here; use an empty string to disable persistent samples",
    )
    parser.add_argument(
        "--load-sample-retention-hours",
        type=float,
        default=24,
        help="Keep this many hours of load samples in the rolling log",
    )
    parser.add_argument(
        "--metrics-db-path",
        default="data/metrics.sqlite",
        help="Append all supervisor metrics to this SQLite database; use an empty string to disable",
    )
    parser.add_argument(
        "--metrics-snapshot-interval",
        type=float,
        default=60,
        help="Seconds between durable supervisor snapshot records",
    )
    parser.add_argument(
        "--metrics-settings-interval",
        type=float,
        default=3600,
        help="Seconds between unchanged device-settings heartbeat records",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        default=not config.display.clear_screen,
        help="Do not clear the terminal before each redraw",
    )
    parser.add_argument(
        "--no-terminal-display",
        action="store_true",
        help="Collect snapshots, metrics, and optional web display data without printing the terminal UI",
    )
    parser.add_argument(
        "--charger-current-taper",
        action="store_true",
        default=_env_bool("CHARGER_CURRENT_TAPER", _env_bool("CLASSIC_CURRENT_TAPER", False)),
        help="Dynamically write volatile charger current limits from BMS SOC/voltage telemetry",
    )
    parser.add_argument(
        "--charger-current-taper-dry-run",
        action="store_true",
        default=_env_bool("CHARGER_CURRENT_TAPER_DRY_RUN", _env_bool("CLASSIC_CURRENT_TAPER_DRY_RUN", False)),
        help="Evaluate and log charger current taper decisions without writing settings",
    )
    parser.add_argument(
        "--charger-current-taper-target",
        choices=["classic", "epever"],
        default=os.getenv("CHARGER_CURRENT_TAPER_TARGET", "classic"),
        help="Charge controller whose current limit is adjusted by the taper loop",
    )
    parser.add_argument(
        "--charger-taper-log-path",
        default=os.getenv("CHARGER_TAPER_LOG_PATH", ""),
        help="Append actionable taper decisions (incl. dry-run) to this CSV",
    )
    parser.add_argument("--classic-current-taper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--classic-current-taper-dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--once", action="store_true", help="Render one snapshot and exit")
    return parser.parse_args()


def add_supervisor_arguments(parser: argparse.ArgumentParser) -> None:
    config = load_config()
    parser.add_argument("--classic-host", default=config.classic.host)
    parser.add_argument("--classic-port", type=int, default=config.classic.port)
    parser.add_argument("--classic-device-id", type=int, default=config.classic.device_id)
    parser.add_argument("--classic-timeout", type=float, default=config.classic.timeout_s)
    parser.add_argument("--no-classic", action="store_true", help="Disable MidNite Classic reads")
    parser.add_argument(
        "--epever-device",
        default=config.epever.device,
        help="Serial device for EPEver RS-485 Modbus RTU; empty disables",
    )
    parser.add_argument("--epever-baud", type=int, default=config.epever.baud)
    parser.add_argument("--epever-unit", type=int, default=config.epever.unit)
    parser.add_argument("--epever-timeout", type=float, default=config.epever.timeout_s)
    parser.add_argument("--battery-can-interface", default="can0", help="SocketCAN battery interface")
    parser.add_argument("--battery-can-bitrate", type=int, default=500000, help="SocketCAN battery interface bitrate")
    parser.add_argument("--battery-can-seconds", type=float, default=1.5, help="Seconds to collect battery CAN frames")
    parser.add_argument(
        "--battery-can-protocol",
        default=config.battery_can.protocol,
        choices=[protocol.value for protocol in BatteryCanProtocol],
        help="Battery CAN decode profile",
    )
    parser.add_argument(
        "--no-battery-can-auto-up",
        action="store_true",
        help="Do not automatically configure and raise a down SocketCAN battery interface",
    )
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
        "--magnum-device",
        default=os.getenv("MAGNUM_DEVICE", ""),
        help="Serial device for the Magnum RS-485 telemetry tap (read-only by policy); empty disables",
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
    parser.add_argument(
        "--inverter-event-log-path",
        default=os.getenv("INVERTER_EVENT_LOG_PATH", ""),
        help="Append inverter on/off and LBCO cut-out events to this CSV",
    )


def build_supervisor(args: argparse.Namespace) -> Supervisor:
    ambient = None
    if not args.no_ambient:
        if args.ambient_kind == "ds18b20":
            ambient = AmbientDs18b20Client(device_id=args.ds18b20_device_id)
        else:
            ambient = AmbientDhtClient(gpio_pin=args.ambient_gpio, sensor_type=args.ambient_kind)

    battery_can_interface = None if args.no_battery_can else args.battery_can_interface

    battery = None
    if battery_can_interface is not None:
        if not args.no_battery_can_auto_up:
            try:
                ensure_socketcan_interface_up(
                    args.battery_can_interface,
                    bitrate=args.battery_can_bitrate,
                    listen_only=True,
                )
            except Exception as exc:  # noqa: BLE001 - keep display alive and show the read failure.
                print(f"Battery CAN auto-up failed: {exc}", file=sys.stderr)
    if battery_can_interface is not None and args.battery_can_interface in socketcan_interfaces():
        battery = BatteryCanClient(
            interface=args.battery_can_interface,
            receive_seconds=args.battery_can_seconds,
            protocol=args.battery_can_protocol,
        )

    # Magnum telemetry tap. Read-only by policy (the OEM remote remains the
    # operating interface; decision 0002), but the path is TX-capable if an
    # inverter-toggle write is ever wanted.
    magnum = MagnumClient(args.magnum_device) if args.magnum_device else None
    epever = (
        EpeverClient(
            device=args.epever_device,
            baud=args.epever_baud,
            unit=args.epever_unit,
            timeout=args.epever_timeout,
        )
        if args.epever_device
        else None
    )

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
        battery_can_interface=battery_can_interface,
        magnum=magnum,
        epever=epever,
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
    load_totals_tracker = LoadTotalsTracker(battery_capacity_ah=args.battery_capacity_ah)
    load_sample_buffer = LoadSampleBuffer(
        path=args.load_sample_log_path or None,
        retention=timedelta(hours=args.load_sample_retention_hours),
    )
    load_summary_tracker = LoadTracker(sample_buffer=load_sample_buffer)
    metric_recorder = MetricRecorder(
        args.metrics_db_path or None,
        snapshot_interval_s=args.metrics_snapshot_interval,
        settings_interval_s=args.metrics_settings_interval,
    )
    snapshot_cache = SnapshotCache()
    weather_service = build_weather_service(args)
    charger_current_taper_enabled = args.charger_current_taper or args.classic_current_taper
    charger_current_taper_dry_run = args.charger_current_taper_dry_run or args.classic_current_taper_dry_run
    charger_current_taper = (
        ChargerCurrentTaperController()
        if charger_current_taper_enabled or charger_current_taper_dry_run
        else None
    )
    if args.web_display:
        start_web_display(args, supervisor, snapshot_cache, weather_service)
    previous_poll_render: str | None = None

    # Per-device actor threads: one thread owns each adapter so a slow or
    # wedged device cannot stall the tick, and writes (charger taper) are
    # queued onto the owning thread. --once keeps the synchronous path so a
    # single probe reads every device exactly once.
    if not args.no_device_readers and not args.once:
        supervisor.start_readers(interval_s=args.interval)
        supervisor.wait_for_initial_readings()

    try:
        while True:
            snapshot = supervisor.read_snapshot()
            apply_charger_current_taper(
                charger_current_taper,
                dry_run=charger_current_taper_dry_run,
                enabled=charger_current_taper_enabled,
                supervisor=supervisor,
                snapshot=snapshot,
                target=args.charger_current_taper_target,
                log_path=args.charger_taper_log_path,
            )
            load_totals = load_totals_tracker.update(snapshot.captured_at, snapshot.battery, snapshot.classic)
            load_summary = load_summary_tracker.update(snapshot)
            snapshot_cache.set(snapshot, load_summary)
            record_metrics(metric_recorder, snapshot, load_summary)
            record_weather_metrics(metric_recorder, weather_service)
            append_ambient_log(args.ambient_log_path, snapshot)
            next_read = time.monotonic() + args.interval
            if args.no_terminal_display:
                if args.once:
                    return 0 if snapshot.ok else 1
                remaining = next_read - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                previous_poll_render = None
                continue
            rendered = ""
            while True:
                rendered = render_snapshot(
                    snapshot,
                    now=datetime.now(snapshot.captured_at.tzinfo),
                    load_totals=load_totals,
                    load_summary=load_summary,
                )
                if not args.no_clear:
                    clear_screen()
                print(highlight_changed_digits(previous_poll_render, rendered))
                if args.once:
                    return 0 if snapshot.ok else 1
                remaining = next_read - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(1.0, remaining))
            previous_poll_render = rendered
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        supervisor.stop_readers()


def start_web_display(
    args: argparse.Namespace,
    supervisor: Supervisor,
    snapshot_cache: SnapshotCache,
    weather_service: WeatherService | None = None,
) -> None:
    thread = Thread(
        target=run_display_server,
        kwargs={
            "supervisor": supervisor,
            "host": args.web_host,
            "port": args.web_port,
            "snapshot_provider": snapshot_cache.get,
            "load_summary_provider": snapshot_cache.get_load_summary,
            "weather_provider": None if weather_service is None else weather_service.get,
            "access_log_path": args.web_access_log_path or None,
        },
        daemon=True,
    )
    thread.start()


def build_weather_service(args: argparse.Namespace) -> WeatherService | None:
    if not args.weather:
        return None
    if args.weather_latitude == 0.0 and args.weather_longitude == 0.0:
        print("Weather disabled: latitude/longitude not configured", file=sys.stderr)
        return None
    return WeatherService(
        WeatherConfig(
            latitude=args.weather_latitude,
            longitude=args.weather_longitude,
            label=args.weather_label,
            refresh=timedelta(minutes=args.weather_refresh_minutes),
            cache_path=args.weather_cache_path or None,
        )
    )


def record_metrics(metric_recorder: MetricRecorder, snapshot, load_summary) -> None:
    try:
        metric_recorder.record_snapshot(snapshot, load_summary=load_summary)
    except Exception as exc:  # noqa: BLE001 - metrics are advisory to the live display.
        print(f"Metrics record failed: {exc}", file=sys.stderr)


def record_weather_metrics(metric_recorder: MetricRecorder, weather_service: WeatherService | None) -> None:
    if weather_service is None:
        return
    try:
        metric_recorder.record_weather(weather_service.get())
    except Exception as exc:  # noqa: BLE001 - weather logging should not affect live supervision.
        print(f"Weather record failed: {exc}", file=sys.stderr)


def apply_charger_current_taper(
    charger_current_taper: ChargerCurrentTaperController | None,
    *,
    dry_run: bool,
    enabled: bool,
    supervisor: Supervisor,
    snapshot,
    target: str = "classic",
    log_path: str = "",
) -> None:
    if charger_current_taper is None:
        return
    try:
        if target == "epever":
            charger = _epever_charger_telemetry(snapshot)
            settings = _epever_current_settings(snapshot)
        else:
            charger = _classic_charger_telemetry(snapshot)
            settings = _classic_current_settings(snapshot)
        decision = charger_current_taper.decide(charger, settings, snapshot.battery)
        if decision.target_current_a is None:
            return
        current = settings.current_limit_a if settings is not None else None
        if decision.should_write:
            append_decision_log(
                log_path,
                dry_run=dry_run or not enabled,
                charge_stage=charger.charge_stage if charger is not None else None,
                battery_voltage_v=charger.voltage_v if charger is not None else None,
                current_limit_a=current,
                decision=decision,
                battery=snapshot.battery,
            )
        if dry_run:
            if decision.should_write:
                print(
                    "Charger current taper dry-run: "
                    f"{current:.1f}A -> {decision.target_current_a:.1f}A ({decision.reason})",
                    file=sys.stderr,
                )
            return
        if not enabled or not decision.should_write:
            return
        if target == "epever":
            if supervisor.epever is None:
                return
            target_current_a = max(1.0, decision.target_current_a)
            supervisor.write_epever_max_charging_current(target_current_a)
        else:
            if supervisor.classic is None:
                return
            target_current_a = decision.target_current_a
            supervisor.write_classic_charge_settings(
                battery_current_limit_a=target_current_a,
                persist=False,
            )
        print(
            f"Charger current taper: {target} "
            f"{current:.1f}A -> {target_current_a:.1f}A ({decision.reason})",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001 - taper should never kill telemetry/display.
        print(f"Charger current taper failed: {exc}", file=sys.stderr)


def _classic_charger_telemetry(snapshot) -> ChargerTelemetry | None:
    if snapshot.classic is None:
        return None
    return ChargerTelemetry(
        voltage_v=snapshot.classic.battery_voltage_v,
        charge_stage=snapshot.classic.canonical_stage.value,
    )


def _classic_current_settings(snapshot) -> ChargerCurrentSettings | None:
    if snapshot.classic_settings is None:
        return None
    return ChargerCurrentSettings(current_limit_a=snapshot.classic_settings.battery_current_limit_a)


def _epever_charger_telemetry(snapshot) -> ChargerTelemetry | None:
    if snapshot.epever is None:
        return None
    return ChargerTelemetry(
        voltage_v=snapshot.epever.battery_voltage_v,
        charge_stage=snapshot.epever.canonical_stage.value,
    )


def _epever_current_settings(snapshot) -> ChargerCurrentSettings | None:
    if snapshot.epever_settings is None or snapshot.epever_settings.max_charging_current_a is None:
        return None
    return ChargerCurrentSettings(current_limit_a=snapshot.epever_settings.max_charging_current_a)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
