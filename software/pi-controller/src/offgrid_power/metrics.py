"""Append-only SQLite metric storage for supervisor snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .supervisor import SupervisorSnapshot
from .web_display import LoadSummary


@dataclass(frozen=True)
class MetricSample:
    captured_at: datetime
    source: str
    metric: str
    value: float | None = None
    text: str | None = None
    unit: str | None = None
    tags: dict[str, str] | None = None


class MetricRecorder:
    def __init__(self, path: str | None = "data/metrics.sqlite") -> None:
        self.path = Path(path) if path else None
        self._initialized = False

    def record_snapshot(
        self,
        snapshot: SupervisorSnapshot,
        load_summary: LoadSummary | None = None,
    ) -> None:
        if self.path is None:
            return

        samples = list(snapshot_metric_samples(snapshot, load_summary))
        if not samples:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            if not self._initialized:
                initialize_metrics_db(connection)
                self._initialized = True
            connection.executemany(
                """
                INSERT INTO metric_samples (
                    captured_at, source, metric, value, text, unit, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        sample.captured_at.isoformat(),
                        sample.source,
                        sample.metric,
                        sample.value,
                        sample.text,
                        sample.unit,
                        json.dumps(sample.tags or {}, sort_keys=True),
                    )
                    for sample in samples
                ],
            )


def initialize_metrics_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metric_samples (
            id INTEGER PRIMARY KEY,
            captured_at TEXT NOT NULL,
            source TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            text TEXT,
            unit TEXT,
            tags_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS metric_samples_metric_time_idx
        ON metric_samples (source, metric, captured_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS metric_samples_time_idx
        ON metric_samples (captured_at)
        """
    )


def snapshot_metric_samples(
    snapshot: SupervisorSnapshot,
    load_summary: LoadSummary | None = None,
) -> Iterable[MetricSample]:
    captured_at = snapshot.captured_at.astimezone()
    yield MetricSample(captured_at, "supervisor", "ok", value=1.0 if snapshot.ok else 0.0)
    yield MetricSample(captured_at, "supervisor", "error_count", value=float(len(snapshot.errors)))
    for index, error in enumerate(snapshot.errors):
        yield MetricSample(captured_at, "supervisor", "error", text=error, tags={"index": str(index)})
    yield MetricSample(captured_at, "supervisor", "status_condition_count", value=float(len(snapshot.status_conditions)))
    for index, condition in enumerate(snapshot.status_conditions):
        yield MetricSample(captured_at, "supervisor", "status_condition", text=condition, tags={"index": str(index)})

    if load_summary is not None:
        yield from _load_samples(captured_at, load_summary)
    if snapshot.classic is not None:
        yield from _classic_samples(snapshot.classic.captured_at.astimezone(), snapshot.classic)
    if snapshot.classic_settings is not None:
        yield from _classic_settings_samples(snapshot.classic_settings.captured_at.astimezone(), snapshot.classic_settings)
    if snapshot.battery is not None:
        yield from _battery_samples(captured_at, snapshot.battery)
    if snapshot.battery_can_health is not None:
        yield from _battery_can_health_samples(captured_at, snapshot.battery_can_health)
    if snapshot.ambient is not None:
        yield from _ambient_samples(snapshot.ambient.captured_at.astimezone(), snapshot.ambient)


def _load_samples(captured_at: datetime, load_summary: LoadSummary) -> Iterable[MetricSample]:
    yield MetricSample(captured_at, "load", "current", value=load_summary.current_a, unit="A")
    yield MetricSample(captured_at, "load", "power", value=float(load_summary.power_w), unit="W")
    if load_summary.rolling_average_a is not None:
        yield MetricSample(captured_at, "load", "rolling_average_current", value=load_summary.rolling_average_a, unit="A")
    if load_summary.rolling_average_w is not None:
        yield MetricSample(captured_at, "load", "rolling_average_power", value=load_summary.rolling_average_w, unit="W")
    autonomy_hours = _hours_text_value(load_summary.remaining_text)
    if autonomy_hours is not None:
        yield MetricSample(captured_at, "load", "estimated_autonomy", value=autonomy_hours, unit="h")


def _classic_samples(captured_at: datetime, classic) -> Iterable[MetricSample]:
    source = "classic.0"
    yield MetricSample(captured_at, source, "battery_voltage", value=classic.battery_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "pv_voltage", value=classic.pv_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "battery_current", value=classic.battery_current_a, unit="A")
    yield MetricSample(captured_at, source, "pv_current", value=classic.pv_current_a, unit="A")
    yield MetricSample(captured_at, source, "battery_power", value=float(classic.battery_power_w), unit="W")
    yield MetricSample(captured_at, source, "daily_energy", value=classic.daily_energy_kwh, unit="kWh")
    yield MetricSample(captured_at, source, "daily_amp_hours", value=float(classic.daily_amp_hours_ah), unit="Ah")
    yield MetricSample(captured_at, source, "lifetime_energy", value=float(classic.lifetime_energy_kwh), unit="kWh")
    yield MetricSample(captured_at, source, "lifetime_amp_hours", value=float(classic.lifetime_amp_hours_ah), unit="Ah")
    yield MetricSample(captured_at, source, "last_voc", value=classic.last_voc_v, unit="V")
    yield MetricSample(captured_at, source, "highest_input_voltage", value=classic.highest_input_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "charge_stage_code", value=float(classic.charge_stage_code))
    yield MetricSample(captured_at, source, "charge_stage", text=classic.charge_stage)
    yield MetricSample(captured_at, source, "state_code", value=float(classic.state_code))
    yield MetricSample(captured_at, source, "state", text=classic.state)
    yield MetricSample(captured_at, source, "info_flags", value=float(classic.info_flags))
    yield MetricSample(captured_at, source, "active_flags", text=", ".join(classic.active_flags))
    yield MetricSample(captured_at, source, "battery_temperature", value=classic.battery_temp_c, unit="C")
    yield MetricSample(captured_at, source, "fet_temperature", value=classic.fet_temp_c, unit="C")
    yield MetricSample(captured_at, source, "pcb_temperature", value=classic.pcb_temp_c, unit="C")


def _classic_settings_samples(captured_at: datetime, settings) -> Iterable[MetricSample]:
    source = "classic.0.settings"
    yield MetricSample(captured_at, source, "battery_current_limit", value=settings.battery_current_limit_a, unit="A")
    yield MetricSample(captured_at, source, "absorb_voltage", value=settings.absorb_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "float_voltage", value=settings.float_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "equalize_voltage", value=settings.equalize_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "sliding_current_limit", value=float(settings.sliding_current_limit_a), unit="A")
    yield MetricSample(captured_at, source, "absorb_time", value=float(settings.absorb_time_s), unit="s")
    yield MetricSample(captured_at, source, "max_temp_comp_voltage", value=settings.max_temp_comp_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "min_temp_comp_voltage", value=settings.min_temp_comp_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "temp_comp", value=settings.temp_comp_mv_per_c_cell, unit="mV/C/cell")
    yield MetricSample(captured_at, source, "mppt_mode_raw", value=float(settings.mppt_mode_raw))
    yield MetricSample(captured_at, source, "aux_function_word", value=float(settings.aux_function_word))


def _battery_samples(captured_at: datetime, battery) -> Iterable[MetricSample]:
    source = "battery"
    if battery.state_of_charge is not None:
        yield MetricSample(captured_at, source, "soc", value=float(battery.state_of_charge.soc_percent), unit="%")
        yield MetricSample(captured_at, source, "soh", value=float(battery.state_of_charge.soh_percent), unit="%")
    if battery.measurements is not None:
        yield MetricSample(captured_at, source, "voltage", value=battery.measurements.voltage_v, unit="V")
        yield MetricSample(captured_at, source, "current", value=battery.measurements.current_a, unit="A")
        yield MetricSample(captured_at, source, "power", value=battery.measurements.voltage_v * battery.measurements.current_a, unit="W")
        yield MetricSample(captured_at, source, "temperature", value=battery.measurements.temperature_c, unit="C")
    if battery.extended_measurements is not None:
        extended = battery.extended_measurements
        if extended.min_cell_voltage_v is not None:
            yield MetricSample(captured_at, source, "min_cell_voltage", value=extended.min_cell_voltage_v, unit="V")
        if extended.max_cell_voltage_v is not None:
            yield MetricSample(captured_at, source, "max_cell_voltage", value=extended.max_cell_voltage_v, unit="V")
        if extended.min_cell_voltage_v is not None and extended.max_cell_voltage_v is not None:
            yield MetricSample(captured_at, source, "cell_voltage_delta", value=(extended.max_cell_voltage_v - extended.min_cell_voltage_v) * 1000, unit="mV")
        if extended.min_cell_temperature_c is not None:
            yield MetricSample(captured_at, source, "min_cell_temperature", value=extended.min_cell_temperature_c, unit="C")
        if extended.max_cell_temperature_c is not None:
            yield MetricSample(captured_at, source, "max_cell_temperature", value=extended.max_cell_temperature_c, unit="C")
        if extended.installed_capacity_ah is not None:
            yield MetricSample(captured_at, source, "installed_capacity", value=extended.installed_capacity_ah, unit="Ah")
    if battery.charge_limits is not None:
        limits = battery.charge_limits
        yield MetricSample(captured_at, source, "charge_voltage_limit", value=limits.charge_voltage_limit_v, unit="V")
        yield MetricSample(captured_at, source, "charge_current_limit", value=limits.charge_current_limit_a, unit="A")
        yield MetricSample(captured_at, source, "discharge_current_limit", value=limits.discharge_current_limit_a, unit="A")
        yield MetricSample(captured_at, source, "discharge_voltage_limit", value=limits.discharge_voltage_limit_v, unit="V")
    if battery.request_flags is not None:
        flags = battery.request_flags
        yield MetricSample(captured_at, source, "charge_enable", value=1.0 if flags.charge_enable else 0.0)
        yield MetricSample(captured_at, source, "discharge_enable", value=1.0 if flags.discharge_enable else 0.0)
        yield MetricSample(captured_at, source, "force_charge_1", value=1.0 if flags.force_charge_1 else 0.0)
        yield MetricSample(captured_at, source, "force_charge_2", value=1.0 if flags.force_charge_2 else 0.0)
        yield MetricSample(captured_at, source, "full_charge_request", value=1.0 if flags.full_charge_request else 0.0)
    if battery.status is not None:
        status = battery.status
        yield MetricSample(captured_at, source, "module_count", value=float(status.module_count))
        yield MetricSample(captured_at, source, "protection_flags", text=", ".join(status.protection_flags))
        yield MetricSample(captured_at, source, "alarm_flags", text=", ".join(status.alarm_flags))
        yield MetricSample(captured_at, source, "manufacturer_marker", text=status.manufacturer_marker)
    if battery.manufacturer is not None:
        yield MetricSample(captured_at, source, "manufacturer", text=battery.manufacturer)


def _battery_can_health_samples(captured_at: datetime, health) -> Iterable[MetricSample]:
    source = "battery.can"
    yield MetricSample(captured_at, source, "ok", value=1.0 if health.ok else 0.0, tags={"interface": health.interface})
    yield MetricSample(captured_at, source, "socketcan_present", value=1.0 if health.socketcan_present else 0.0, tags={"interface": health.interface})
    yield MetricSample(captured_at, source, "dfu_device_count", value=float(len(health.dfu_devices)), tags={"interface": health.interface})
    yield MetricSample(captured_at, source, "status", text=health.status_message(), tags={"interface": health.interface})


def _ambient_samples(captured_at: datetime, ambient) -> Iterable[MetricSample]:
    yield MetricSample(captured_at, "ambient", "temperature", value=ambient.temperature_c, unit="C")
    if ambient.humidity_percent is not None:
        yield MetricSample(captured_at, "ambient", "humidity", value=ambient.humidity_percent, unit="%")


def _hours_text_value(text: str | None) -> float | None:
    if text is None or not text.endswith("h"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None
