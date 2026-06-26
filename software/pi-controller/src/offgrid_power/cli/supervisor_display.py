"""Command-line terminal display for the Pi supervisor."""

from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timedelta
import os
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
from offgrid_power.charge_allocator import (
    AllocationOverride,
    ChargeAllocatorConfig,
    ChargeAllocationDecision,
    ChargeCurrentAllocator,
    ChargerAllocationInput,
    ChargerAllocationTarget,
    allocation_detail,
    charge_allocation_event,
    charge_enable_write_event,
    charge_limit_write_event,
)
from offgrid_power.charge_ceiling import ChargeCeiling, ChargeCeilingConfig
from offgrid_power.charger_taper import (
    ChargerCurrentSettings,
    ChargerCurrentTaperController,
    ChargerTelemetry,
    taper_decision_event,
)
from offgrid_power.classic import ClassicClient
from offgrid_power.epever import EpeverClient
from offgrid_power.magnum import InverterEventTracker, MagnumClient
from offgrid_power.network_monitor import WanReachabilityTracker
from offgrid_power.config import load_config, load_relay_config
from offgrid_power.relay import RelayController
from offgrid_power.relay_control import RelaySupervisor
from offgrid_power.load import estimate_load_current_a, LoadTotalsTracker
from offgrid_power.metrics import MetricRecorder
from offgrid_power.runtime_state import load_ccl_scaling_factor, save_ccl_scaling_factor
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
    parser.add_argument(
        "--unavailable-after-seconds",
        type=float,
        default=config.display.unavailable_after_seconds,
        help="Drop a device's cached readings (render 'No data') after this many seconds without a good read",
    )
    parser.add_argument(
        "--magnum-stale-after-seconds",
        type=float,
        default=config.display.magnum_stale_after_seconds,
        help="Warn after this many seconds without a good Magnum RS485 read (default 60)",
    )
    parser.add_argument(
        "--lan-gateway",
        default=config.network.lan_gateway,
        help="LAN gateway address to probe for reachability (default 192.168.0.1)",
    )
    parser.add_argument(
        "--lan-check-interval-seconds",
        type=float,
        default=config.network.lan_check_interval_s,
        help="Seconds between LAN/WAN reachability probes (default 30)",
    )
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
        "--metrics-db-path",
        default="data/metrics.sqlite",
        help="Append all supervisor metrics to this SQLite database; use an empty string to disable",
    )
    parser.add_argument(
        "--metrics-db-mountpoint",
        default="",
        help="Use the metrics DB only while this mountpoint is mounted (guards a removable store); empty disables the check",
    )
    parser.add_argument(
        "--metrics-fallback-db-path",
        default="",
        help="Record here while the primary store is unmounted/unwritable; merged back and removed on recovery",
    )
    parser.add_argument(
        "--metrics-snapshot-interval",
        type=float,
        default=60,
        help="Seconds between durable supervisor snapshot records",
    )
    parser.add_argument(
        "--runtime-state-path",
        default="",
        help="JSON file persisting operator runtime overrides (CCL scaling factor) across restarts; "
        "empty disables persistence (the env default is used each start)",
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
    parser.add_argument("--classic-current-taper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--classic-current-taper-dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--charge-allocation-dry-run",
        action="store_true",
        help="Evaluate the system-level CCL charge-current allocator each cycle and log the "
        "decision as a telemetry event, without writing any charger limits",
    )
    parser.add_argument(
        "--charge-allocation",
        action="store_true",
        help="LIVE: evaluate the CCL allocator and WRITE per-controller current limits "
        "(Classic limit, EPEver max-current register + charge coil). Sole current writer; "
        "cannot run with the live charger taper.",
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


def main() -> int:
    args = parse_args()
    supervisor = build_supervisor(args)
    load_totals_tracker = LoadTotalsTracker(battery_capacity_ah=args.battery_capacity_ah)
    metric_recorder = MetricRecorder(
        args.metrics_db_path or None,
        snapshot_interval_s=args.metrics_snapshot_interval,
        mountpoint=args.metrics_db_mountpoint or None,
        fallback_path=args.metrics_fallback_db_path or None,
    )
    # The buffer is in-memory; the metric store is the durable copy, so a
    # restart re-seeds the rolling window from it (best-effort).
    load_sample_buffer = LoadSampleBuffer()
    load_sample_buffer.seed(metric_recorder.recent_load_samples(window=load_sample_buffer.retention))
    load_summary_tracker = LoadTracker(
        midnight_soc_provider=metric_recorder.midnight_soc_percent,
        sample_buffer=load_sample_buffer,
    )
    inverter_event_tracker = InverterEventTracker()
    wan_reachability_tracker = WanReachabilityTracker()
    snapshot_cache = SnapshotCache()
    weather_service = build_weather_service(args)
    charger_current_taper_enabled = args.charger_current_taper or args.classic_current_taper
    charger_current_taper_dry_run = args.charger_current_taper_dry_run or args.classic_current_taper_dry_run
    charger_current_taper = (
        ChargerCurrentTaperController()
        if charger_current_taper_enabled or charger_current_taper_dry_run
        else None
    )
    if args.charge_allocation and charger_current_taper_enabled:
        parser.error(
            "--charge-allocation (live) cannot run with the live charger taper "
            "(--charger-current-taper / CHARGER_CURRENT_TAPER). The allocator is the "
            "sole current-limit writer; disable the taper first."
        )
    # CCL scaling factor: a persisted operator override (if any) wins over the
    # env default; the env default applies only when the JSON has no value.
    state_path = args.runtime_state_path or None
    ceiling_config = _config_from_env(ChargeCeilingConfig, "CHARGE_CEILING_")
    persisted_scaling = load_ccl_scaling_factor(state_path)
    if persisted_scaling is not None:
        ceiling_config = dataclasses.replace(ceiling_config, bms_ccl_scaling_factor=persisted_scaling)
    on_scaling_change = (lambda value: save_ccl_scaling_factor(state_path, value)) if state_path else None
    allocation_override = AllocationOverride() if args.charge_allocation or args.charge_allocation_dry_run else None
    snapshot_cache._allocation_override = allocation_override
    charge_allocation_logger = (
        ChargeAllocationLogger(
            ChargeCurrentAllocator(_config_from_env(ChargeAllocatorConfig, "CHARGE_ALLOC_")),
            supervisor=supervisor,
            live=args.charge_allocation,
            ceiling_config=ceiling_config,
            on_scaling_change=on_scaling_change,
            heartbeat_s=_env_float("CHARGE_ALLOC_HEARTBEAT_S", 300.0),
            target_deadband_a=_env_float("CHARGE_ALLOC_TARGET_DEADBAND_A", 5.0),
            target_quantum_a=_env_float("CHARGE_ALLOC_TARGET_QUANTUM_A", 5.0),
            classic_sleep_debounce_s=_env_float("CHARGE_ALLOC_CLASSIC_SLEEP_DEBOUNCE_S", 180.0),
            epever_sleep_debounce_s=_env_float("CHARGE_ALLOC_EPEVER_SLEEP_DEBOUNCE_S", 180.0),
            override=allocation_override,
        )
        if args.charge_allocation or args.charge_allocation_dry_run
        else None
    )
    relay_cfg = load_relay_config()
    relay_controller = RelayController(
        heat_fan_gpio=relay_cfg.heat_fan_gpio,
        charge_disable_gpio=relay_cfg.charge_disable_gpio,
    )
    relay_supervisor = RelaySupervisor(relay_controller)
    if args.web_display:
        start_web_display(
            args,
            supervisor,
            snapshot_cache,
            weather_service,
            charge_ceiling=charge_allocation_logger.ceiling if charge_allocation_logger is not None else None,
            allocation_override=allocation_override,
            relay_controller=relay_controller,
        )
    previous_poll_render: str | None = None

    # Per-device actor threads: one thread owns each adapter so a slow or
    # wedged device cannot stall the tick, and writes (charger taper) are
    # queued onto the owning thread. --once keeps the synchronous path so a
    # single probe reads every device exactly once.
    if not args.no_device_readers and not args.once:
        supervisor.start_readers(
            interval_s=args.interval,
            expire_after_s=args.unavailable_after_seconds,
            magnum_stale_after_s=args.magnum_stale_after_seconds,
        )
        supervisor.start_network_monitor(
            gateway=args.lan_gateway,
            interval_s=args.lan_check_interval_seconds,
        )
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
                recorder=metric_recorder,
            )
            allocation_detail_payload = None
            allocation_decision = None
            if charge_allocation_logger is not None:
                allocation_decision = charge_allocation_logger.record(snapshot, metric_recorder)
                if allocation_decision is not None:
                    allocation_detail_payload = allocation_detail(
                        allocation_decision,
                        dry_run=not charge_allocation_logger.live,
                        ccl_scaling_factor=charge_allocation_logger.ceiling.scaling_factor,
                    )
            relay_supervisor.update(snapshot, allocation_decision, allocation_override)
            # Derive the EPEver "today" from its monotonic lifetime total (its own
            # daily register doesn't reset); the load cumulative needs it too, so
            # build the display copy before computing the load summary.
            display_snapshot = _with_derived_epever_today(snapshot, metric_recorder)
            load_totals = load_totals_tracker.update(snapshot.captured_at, snapshot.battery, snapshot.classic)
            load_summary = load_summary_tracker.update(display_snapshot)
            # Record the raw device telemetry (keeps the raw EPEver generated_today
            # register in the store for diagnosis); the load summary already uses
            # the derived value.
            record_metrics(metric_recorder, snapshot, load_summary)
            snapshot_cache.set(display_snapshot, load_summary, allocation=allocation_detail_payload)
            record_weather_metrics(metric_recorder, weather_service)
            record_inverter_event(metric_recorder, inverter_event_tracker, snapshot)
            record_wan_transition(metric_recorder, wan_reachability_tracker, snapshot)
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
                    allocation=allocation_detail_payload,
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
        supervisor.stop_network_monitor()


def start_web_display(
    args: argparse.Namespace,
    supervisor: Supervisor,
    snapshot_cache: SnapshotCache,
    weather_service: WeatherService | None = None,
    charge_ceiling: ChargeCeiling | None = None,
    allocation_override: AllocationOverride | None = None,
    relay_controller: RelayController | None = None,
) -> None:
    thread = Thread(
        target=run_display_server,
        kwargs={
            "supervisor": supervisor,
            "host": args.web_host,
            "port": args.web_port,
            "snapshot_provider": snapshot_cache.get,
            "load_summary_provider": snapshot_cache.get_load_summary,
            "allocation_provider": snapshot_cache.get_allocation,
            "weather_provider": None if weather_service is None else weather_service.get,
            "weather_refresh_hook": None if weather_service is None else weather_service.request_refresh,
            "charge_ceiling": charge_ceiling,
            "allocation_override": allocation_override,
            "relay_controller": relay_controller,
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


def record_inverter_event(
    metric_recorder: MetricRecorder,
    tracker: InverterEventTracker,
    snapshot,
) -> None:
    try:
        event = tracker.observe(snapshot.magnum, snapshot.battery)
        if event is not None:
            metric_recorder.record_event(event)
    except Exception as exc:  # noqa: BLE001 - event logging should not affect live supervision.
        print(f"Inverter event record failed: {exc}", file=sys.stderr)


def record_wan_transition(
    metric_recorder: MetricRecorder,
    tracker: WanReachabilityTracker,
    snapshot,
) -> None:
    try:
        event = tracker.observe(snapshot.wan_reachable)
        if event is not None:
            metric_recorder.record_event(event)
    except Exception as exc:  # noqa: BLE001 - event logging should not affect live supervision.
        print(f"WAN reachability event record failed: {exc}", file=sys.stderr)


def apply_charger_current_taper(
    charger_current_taper: ChargerCurrentTaperController | None,
    *,
    dry_run: bool,
    enabled: bool,
    supervisor: Supervisor,
    snapshot,
    target: str = "classic",
    recorder: MetricRecorder | None = None,
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
        if decision.should_write and recorder is not None:
            recorder.record_event(
                taper_decision_event(
                    dry_run=dry_run or not enabled,
                    target=target,
                    charge_stage=charger.charge_stage if charger is not None else None,
                    battery_voltage_v=charger.voltage_v if charger is not None else None,
                    current_limit_a=current,
                    decision=decision,
                    battery=snapshot.battery,
                    captured_at=snapshot.captured_at,
                )
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


# Per-controller current ceilings are operator knobs (CHARGE_ALLOC_CLASSIC_MAX_A
# / CHARGE_ALLOC_EPEVER_MAX_A). Device telemetry can be stale/miscalibrated, so
# allocator write ceilings come from explicit config.
# Charger state is the allocation availability signal. Unloaded PV voltage can
# remain high long after an array can deliver useful power, especially at
# twilight, so voltage is kept as telemetry rather than the release trigger.
_PV_PRESENT_MARGIN_V = 2.0
_ACTIVE_CHARGE_STAGES = {"Bulk", "Absorb", "Float", "Equalize"}


class ChargeAllocationLogger:
    """Evaluate the CCL allocator each tick, log the decision, and -- when live --
    write the per-controller current limits.

    Logs on a *material change* (reason, budget, or any per-charger target /
    disable) plus a periodic heartbeat, so the event store captures every
    transition and a steady-state pulse without a row every poll. Best-effort:
    a write or eval failure prints and is swallowed, never killing telemetry.

    When ``live``, the allocator is the sole current-limit writer (the legacy
    taper must not also be live -- enforced at startup). Computed targets are
    deliberately sticky/coarse before logging or writing; the EPEver charge coil
    is reconciled to the disable intent (toggled only on change).
    """

    def __init__(
        self,
        allocator: ChargeCurrentAllocator,
        *,
        heartbeat_s: float = 300.0,
        supervisor: Supervisor | None = None,
        live: bool = False,
        ceiling_config: ChargeCeilingConfig | None = None,
        on_scaling_change=None,
        target_deadband_a: float = 5.0,
        target_quantum_a: float = 5.0,
        classic_sleep_debounce_s: float = 180.0,
        epever_sleep_debounce_s: float = 180.0,
        override: AllocationOverride | None = None,
    ) -> None:
        self.allocator = allocator
        self.heartbeat_s = heartbeat_s
        self.supervisor = supervisor
        self.live = live
        # Stateful (full-charge latch); evaluated once per cycle here.
        self.ceiling = ChargeCeiling(ceiling_config, on_scaling_change=on_scaling_change)
        self.target_deadband_a = max(0.0, target_deadband_a)
        self.target_quantum_a = max(1.0, target_quantum_a)
        self._sleep_debounce_s = {
            "classic": classic_sleep_debounce_s,
            "epever": epever_sleep_debounce_s,
        }
        self._last_signature: tuple | None = None
        self._last_logged_monotonic: float | None = None
        self._epever_charging_state: bool | None = None
        self._inactive_since: dict[str, datetime] = {}
        self.override = override

    def _debounced_inputs(
        self, chargers: list[ChargerAllocationInput], captured_at: datetime
    ) -> list[ChargerAllocationInput]:
        out: list[ChargerAllocationInput] = []
        for charger in chargers:
            debounce_s = self._sleep_debounce_s.get(charger.name)
            if debounce_s is None:
                out.append(charger)
                continue
            if charger.active:
                self._inactive_since.pop(charger.name, None)
                out.append(charger)
                continue
            inactive_since = self._inactive_since.setdefault(charger.name, captured_at)
            elapsed_s = max(0.0, (captured_at - inactive_since).total_seconds())
            debounced_active = elapsed_s < debounce_s
            out.append(dataclasses.replace(charger, active=debounced_active))
        return out

    def record(self, snapshot, recorder: MetricRecorder | None):
        try:
            chargers = self._debounced_inputs(
                _allocation_inputs(snapshot),
                snapshot.captured_at,
            )
            if not chargers:
                return None
            battery = snapshot.battery
            bms_ccl_a = battery_current_a = None
            # Live writes must fail conservative: if the BMS charge-enable flag is
            # unreadable, treat as disabled (stop). Dry-run can assume enabled to
            # produce a useful trace.
            charge_enabled = not self.live
            if battery is not None:
                if battery.charge_limits is not None:
                    bms_ccl_a = battery.charge_limits.charge_current_limit_a
                if battery.measurements is not None:
                    battery_current_a = battery.measurements.current_a
                if battery.request_flags is not None:
                    charge_enabled = battery.request_flags.charge_enable
            ceiling = self.ceiling.evaluate(battery, charge_enabled=charge_enabled)
            decision = self.allocator.decide(
                bms_ccl_a=bms_ccl_a,
                charge_enabled=True,
                battery_current_a=battery_current_a,
                load_current_a=estimate_load_current_a(snapshot),
                chargers=chargers,
                charge_ceiling_a=ceiling.ceiling_a,
                charge_ceiling_reason=ceiling.reason,
            )
            decision = self._stabilized_decision(decision, chargers)
            if recorder is not None and self._should_log(decision):
                recorder.record_event(
                    charge_allocation_event(
                        decision, dry_run=not self.live, captured_at=snapshot.captured_at
                    )
                )
            if self.live and self.supervisor is not None:
                self._apply(
                    decision,
                    {c.name: c.current_limit_a for c in chargers},
                    recorder=recorder,
                    captured_at=snapshot.captured_at,
                )
            return decision
        except Exception as exc:  # noqa: BLE001 - allocation must never kill telemetry.
            print(f"Charge allocation failed: {exc}", file=sys.stderr)
            return None

    def _stabilized_decision(
        self,
        decision: ChargeAllocationDecision,
        chargers: list[ChargerAllocationInput],
    ) -> ChargeAllocationDecision:
        charger_by_name = {charger.name: charger for charger in chargers}
        targets: dict[str, ChargerAllocationTarget] = {}
        equal_rebalance = self._needs_equal_rebalance(decision, charger_by_name)
        for name, target in decision.targets.items():
            charger = charger_by_name.get(name)
            if charger is None:
                targets[name] = target
                continue
            targets[name] = self._stabilized_target(
                decision.reason,
                charger,
                target,
                bypass_deadband=equal_rebalance,
            )
        return dataclasses.replace(decision, targets=targets)

    def _needs_equal_rebalance(
        self,
        decision: ChargeAllocationDecision,
        charger_by_name: dict[str, ChargerAllocationInput],
    ) -> bool:
        if decision.weight_basis != "equal":
            return False
        current_limits = [
            charger.current_limit_a
            for name, target in decision.targets.items()
            if (
                (charger := charger_by_name.get(name)) is not None
                and charger.current_limit_a is not None
                and target.target_current_a is not None
                and not target.disable
            )
        ]
        if len(current_limits) < 2:
            return False
        return max(current_limits) - min(current_limits) >= self.target_deadband_a

    def _stabilized_target(
        self,
        global_reason: str,
        charger: ChargerAllocationInput,
        target: ChargerAllocationTarget,
        *,
        bypass_deadband: bool = False,
    ) -> ChargerAllocationTarget:
        raw = target.target_current_a
        current = charger.current_limit_a
        if raw is None or target.disable or self._immediate_limit_reason(global_reason, target.reason):
            return target
        if current is None:
            return target

        stable = raw
        if not bypass_deadband and abs(raw - current) < self.target_deadband_a:
            stable = current
        else:
            stable = raw if bypass_deadband else self._quantized_target(raw, charger.min_current_a, charger.max_current_a)

        should_write = abs(current - stable) >= self.allocator.config.min_write_delta_a
        return dataclasses.replace(target, target_current_a=stable, should_write=should_write)

    def _quantized_target(self, target_a: float, min_current_a: float, max_current_a: float) -> float:
        if target_a <= 0.0:
            return 0.0
        quantized = round(target_a / self.target_quantum_a) * self.target_quantum_a
        quantized = max(min_current_a, min(max_current_a, quantized))
        if target_a > 0.0 and quantized <= 0.0:
            quantized = max(min_current_a, 1.0)
        return float(round(quantized))

    @staticmethod
    def _immediate_limit_reason(global_reason: str, target_reason: str) -> bool:
        reasons = {global_reason, target_reason}
        if reasons & {
            "BMS charge disabled",
            "BMS CCL is zero",
            "full-charge latch",
            "cell safety latch",
            "feedback_clamp",
        }:
            return True
        return any(
            isinstance(reason, str)
            and (reason.startswith("max cell ") or reason.startswith("cell delta "))
            for reason in reasons
        )

    def _apply(
        self,
        decision,
        current_limits: dict,
        *,
        recorder: MetricRecorder | None = None,
        captured_at: datetime | None = None,
    ) -> None:
        targets = self.override.apply(decision.targets) if self.override is not None else decision.targets
        # Reconcile the EPEver charge coil to the disable intent, toggling only on
        # change (the limit register can't reach 0, so off/on is the coil's job).
        epever = targets.get("epever")
        if epever is not None:
            want_on = not epever.disable
            if want_on != self._epever_charging_state:
                previous = self._epever_charging_state
                try:
                    self.supervisor.set_epever_charging(want_on)
                    self._epever_charging_state = want_on
                    self._record_control_event(
                        recorder,
                        charge_enable_write_event(
                            controller="epever",
                            enabled=want_on,
                            previous_enabled=previous,
                            reason=epever.reason,
                            success=True,
                            captured_at=captured_at,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._record_control_event(
                        recorder,
                        charge_enable_write_event(
                            controller="epever",
                            enabled=want_on,
                            previous_enabled=previous,
                            reason=epever.reason,
                            success=False,
                            error=str(exc),
                            captured_at=captured_at,
                        ),
                    )
                    print(f"Charge allocation: epever coil -> {want_on} failed: {exc}", file=sys.stderr)
        for name, target in targets.items():
            if not target.should_write or target.target_current_a is None:
                continue
            write_current_a = target.target_current_a
            if target.disable:
                write_current_a = 0.0
            elif name == "epever":
                write_current_a = max(1.0, target.target_current_a)
            try:
                if target.disable:
                    if name != "classic":
                        # epever disable is the coil, handled above.
                        continue
                    self.supervisor.write_classic_charge_settings(
                        battery_current_limit_a=0.0, persist=False
                    )
                elif name == "epever":
                    self.supervisor.write_epever_max_charging_current(max(1.0, target.target_current_a))
                elif name == "classic":
                    self.supervisor.write_classic_charge_settings(
                        battery_current_limit_a=target.target_current_a, persist=False
                    )
                else:
                    continue
                self._record_control_event(
                    recorder,
                    charge_limit_write_event(
                        controller=name,
                        target_a=write_current_a,
                        previous_a=current_limits.get(name),
                        reason=target.reason,
                        disable=target.disable,
                        success=True,
                        captured_at=captured_at,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._record_control_event(
                    recorder,
                    charge_limit_write_event(
                        controller=name,
                        target_a=write_current_a,
                        previous_a=current_limits.get(name),
                        reason=target.reason,
                        disable=target.disable,
                        success=False,
                        error=str(exc),
                        captured_at=captured_at,
                    ),
                )
                print(f"Charge allocation: {name} write failed: {exc}", file=sys.stderr)

    def _record_control_event(self, recorder: MetricRecorder | None, event) -> None:
        if recorder is None:
            return
        try:
            recorder.record_event(event)
        except Exception as exc:  # noqa: BLE001 - never let telemetry kill control.
            print(f"Charge allocation event record failed: {exc}", file=sys.stderr)

    def _should_log(self, decision) -> bool:
        signature = (
            decision.reason,
            decision.weight_basis,
            decision.budget_a,
            decision.charge_ceiling_a,
            tuple(
                sorted(
                    (name, target.target_current_a, target.disable)
                    for name, target in decision.targets.items()
                )
            ),
        )
        now = time.monotonic()
        if signature != self._last_signature:
            self._last_signature = signature
            self._last_logged_monotonic = now
            return True
        if self._last_logged_monotonic is None or now - self._last_logged_monotonic >= self.heartbeat_s:
            self._last_logged_monotonic = now
            return True
        return False


def _allocation_inputs(snapshot) -> list[ChargerAllocationInput]:
    chargers: list[ChargerAllocationInput] = []
    if snapshot.classic is not None:
        classic = snapshot.classic
        limit = (
            snapshot.classic_settings.battery_current_limit_a
            if snapshot.classic_settings is not None
            else None
        )
        chargers.append(
            ChargerAllocationInput(
                name="classic",
                actual_current_a=classic.battery_current_a,
                current_limit_a=limit,
                max_current_a=_env_float("CHARGE_ALLOC_CLASSIC_MAX_A", 80.0),
                pv_power_w=_pv_power_w(classic.pv_voltage_v, classic.pv_current_a),
                min_current_a=0.0,
                active=classic.canonical_stage.value in _ACTIVE_CHARGE_STAGES,
            )
        )
    if snapshot.epever is not None:
        epever = snapshot.epever
        limit = (
            snapshot.epever_settings.max_charging_current_a
            if snapshot.epever_settings is not None
            else None
        )
        chargers.append(
            ChargerAllocationInput(
                name="epever",
                actual_current_a=epever.battery_current_a,
                current_limit_a=limit,
                max_current_a=_env_float("CHARGE_ALLOC_EPEVER_MAX_A", 100.0),
                pv_power_w=epever.pv_power_w,
                min_current_a=1.0,  # 0x9013 floors at 1 A
                active=epever.canonical_stage.value in _ACTIVE_CHARGE_STAGES,
            )
        )
    return chargers


EPEVER_TODAY_UNAVAILABLE = "unavailable, midnight cumulative energy was not logged"


def _with_derived_epever_today(snapshot, recorder: MetricRecorder | None):
    """Return a display copy of the snapshot whose EPEver generated-today is
    derived from its monotonic lifetime total minus the total at local midnight.

    The EPEver's own daily register does not reset reliably (RTC didn't stick),
    so it must never be shown raw. When the lifetime total or the midnight
    baseline is unavailable (e.g. supervisor down over midnight, or the baseline
    sample was pruned), the derived value is set to None and flagged unavailable
    rather than leaking the misleading raw register. The raw register is still
    recorded separately for diagnosis."""
    epever = snapshot.epever
    if epever is None or recorder is None:
        return snapshot
    day = snapshot.captured_at.astimezone().date()
    midnight_total = (
        recorder.midnight_metric_value("epever.1", "generated_total", day)
        if epever.generated_total_kwh is not None
        else None
    )
    if epever.generated_total_kwh is None or midnight_total is None:
        return dataclasses.replace(
            snapshot,
            epever=dataclasses.replace(
                epever,
                generated_today_kwh=None,
                generated_today_unavailable_reason=EPEVER_TODAY_UNAVAILABLE,
            ),
        )
    derived = round(max(0.0, epever.generated_total_kwh - midnight_total), 2)
    return dataclasses.replace(snapshot, epever=dataclasses.replace(epever, generated_today_kwh=derived))


def _can_charge(pv_voltage_v: float | None, bus_voltage_v: float | None) -> bool:
    if pv_voltage_v is None or bus_voltage_v is None:
        return False
    return pv_voltage_v > bus_voltage_v + _PV_PRESENT_MARGIN_V


def _pv_power_w(voltage_v: float | None, current_a: float | None) -> float | None:
    if voltage_v is None or current_a is None:
        return None
    return round(voltage_v * current_a, 1)


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


def _config_from_env(config_cls, prefix: str):
    """Build a (float-field) config dataclass, overriding any field from
    ``<prefix><FIELD_NAME_UPPER>`` in the environment. Unset/blank -> the
    dataclass default; non-numeric -> ignored with a warning."""
    overrides: dict = {}
    for field in dataclasses.fields(config_cls):
        raw = os.getenv(prefix + field.name.upper())
        if raw in (None, ""):
            continue
        try:
            overrides[field.name] = float(raw)
        except ValueError:
            print(f"Ignoring non-numeric {prefix}{field.name.upper()}={raw!r}", file=sys.stderr)
    return config_cls(**overrides)


if __name__ == "__main__":
    raise SystemExit(main())
