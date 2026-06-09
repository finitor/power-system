"""Cadenced SQLite storage for supervisor snapshots and device settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .supervisor import SupervisorSnapshot
from .weather import WeatherReport
from .web_display import LoadSummary, snapshot_api_payload


@dataclass(frozen=True)
class MetricSample:
    captured_at: datetime
    source: str
    metric: str
    value: float | None = None
    text: str | None = None
    unit: str | None = None
    tags: dict[str, str] | None = None

    def sample_id(self) -> str:
        payload = {
            "captured_at": self.captured_at.isoformat(),
            "source": self.source,
            "metric": self.metric,
            "value": self.value,
            "text": self.text,
            "unit": self.unit,
            "tags": self.tags or {},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class MetricRecorder:
    def __init__(
        self,
        path: str | None = "data/metrics.sqlite",
        snapshot_interval_s: float = 60,
        settings_interval_s: float = 3600,
    ) -> None:
        self.path = Path(path) if path else None
        self._initialized = False
        self.snapshot_interval = timedelta(seconds=snapshot_interval_s)
        self.settings_interval = timedelta(seconds=settings_interval_s)
        self._last_snapshot_recorded_at: datetime | None = None
        self._last_settings_hash_by_device: dict[str, str] = {}
        self._last_settings_recorded_at_by_device: dict[str, datetime] = {}
        self._last_weather_recorded_at: datetime | None = None

    def record_snapshot(
        self,
        snapshot: SupervisorSnapshot,
        load_summary: LoadSummary | None = None,
    ) -> None:
        if self.path is None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=60) as connection:
            connection.execute("PRAGMA busy_timeout = 60000")
            if not self._initialized:
                initialize_metrics_db(connection)
                self._initialized = True
            if self._should_record_snapshot(snapshot.captured_at):
                record_supervisor_snapshot(connection, snapshot, load_summary)
                self._last_snapshot_recorded_at = snapshot.captured_at
            if snapshot.classic_settings is not None:
                self._record_settings_if_needed(connection, "classic.0", snapshot.classic_settings)

    def record_weather(self, report: WeatherReport | None) -> None:
        if self.path is None or report is None or report.stale or not report.data:
            return
        if self._last_weather_recorded_at == report.fetched_at:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=60) as connection:
            connection.execute("PRAGMA busy_timeout = 60000")
            if not self._initialized:
                initialize_metrics_db(connection)
                self._initialized = True
            record_weather_snapshot(connection, report)
            self._last_weather_recorded_at = report.fetched_at

    def _should_record_snapshot(self, captured_at: datetime) -> bool:
        if self._last_snapshot_recorded_at is None:
            return True
        return captured_at - self._last_snapshot_recorded_at >= self.snapshot_interval

    def _record_settings_if_needed(self, connection: sqlite3.Connection, device_id: str, settings) -> None:
        captured_at = settings.captured_at
        settings_payload = classic_settings_payload(settings)
        settings_json = json.dumps(settings_payload, sort_keys=True, separators=(",", ":"))
        settings_hash = hashlib.sha256(settings_json.encode("utf-8")).hexdigest()
        last_hash = self._last_settings_hash_by_device.get(device_id)
        last_recorded_at = self._last_settings_recorded_at_by_device.get(device_id)

        reason = None
        if last_hash is None:
            reason = "startup"
        elif settings_hash != last_hash:
            reason = "changed"
        elif last_recorded_at is None or captured_at - last_recorded_at >= self.settings_interval:
            reason = "hourly"

        if reason is None:
            return

        record_device_settings_snapshot(
            connection,
            captured_at=captured_at,
            device_id=device_id,
            settings_hash=settings_hash,
            reason=reason,
            settings_json=settings_json,
        )
        self._last_settings_hash_by_device[device_id] = settings_hash
        self._last_settings_recorded_at_by_device[device_id] = captured_at


def initialize_metrics_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS supervisor_snapshots (
            id INTEGER PRIMARY KEY,
            captured_at TEXT NOT NULL,
            ok INTEGER NOT NULL,
            status TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS supervisor_snapshots_time_idx
        ON supervisor_snapshots (captured_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS supervisor_snapshots_status_time_idx
        ON supervisor_snapshots (status, captured_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS device_settings_snapshots (
            id INTEGER PRIMARY KEY,
            captured_at TEXT NOT NULL,
            device_id TEXT NOT NULL,
            settings_hash TEXT NOT NULL,
            reason TEXT NOT NULL,
            settings_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_snapshots (
            id INTEGER PRIMARY KEY,
            captured_at TEXT NOT NULL,
            provider TEXT NOT NULL,
            location_label TEXT NOT NULL,
            temperature_c REAL,
            apparent_temperature_c REAL,
            relative_humidity_percent REAL,
            cloud_cover_percent REAL,
            precipitation_mm REAL,
            rain_mm REAL,
            snowfall_cm REAL,
            wind_speed_kmh REAL,
            wind_gust_kmh REAL,
            wind_direction_deg REAL,
            weather_code INTEGER,
            shortwave_radiation_w_m2 REAL,
            direct_radiation_w_m2 REAL,
            diffuse_radiation_w_m2 REAL,
            direct_normal_irradiance_w_m2 REAL,
            sunrise TEXT,
            sunset TEXT,
            moon_phase REAL,
            aurora_probability_percent REAL,
            aurora_forecast_time TEXT,
            raw_json TEXT NOT NULL
        )
        """
    )
    _ensure_weather_columns(connection)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS weather_snapshots_time_idx
        ON weather_snapshots (captured_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS device_settings_time_idx
        ON device_settings_snapshots (device_id, captured_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS export_batches (
            batch_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            uploaded_at TEXT,
            object_key TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS export_batch_records (
            batch_id TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            PRIMARY KEY (record_type, record_id),
            FOREIGN KEY (batch_id) REFERENCES export_batches(batch_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS export_batch_records_batch_idx
        ON export_batch_records (batch_id)
        """
    )
    create_export_status_views(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metric_samples (
            id INTEGER PRIMARY KEY,
            sample_id TEXT,
            captured_at TEXT NOT NULL,
            source TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            text TEXT,
            unit TEXT,
            tags_json TEXT NOT NULL DEFAULT '{}',
            exported_at TEXT,
            export_batch_id TEXT
        )
        """
    )
    _ensure_metric_export_columns(connection)
    _backfill_sample_ids(connection)
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS metric_samples_sample_id_idx
        ON metric_samples (sample_id)
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
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS metric_samples_export_idx
        ON metric_samples (exported_at, id)
        """
    )


def create_export_status_views(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE VIEW IF NOT EXISTS supervisor_snapshots_export_status AS
        SELECT
            snapshots.id,
            snapshots.captured_at,
            snapshots.ok,
            snapshots.status,
            records.batch_id AS export_batch_id,
            batches.uploaded_at AS exported_at,
            batches.object_key AS export_object_key,
            snapshots.snapshot_json
        FROM supervisor_snapshots snapshots
        LEFT JOIN export_batch_records records
          ON records.record_type = 'supervisor_snapshot'
         AND records.record_id = snapshots.id
        LEFT JOIN export_batches batches
          ON batches.batch_id = records.batch_id
        """
    )
    connection.execute(
        """
        CREATE VIEW IF NOT EXISTS device_settings_export_status AS
        SELECT
            settings.id,
            settings.captured_at,
            settings.device_id,
            settings.settings_hash,
            settings.reason,
            records.batch_id AS export_batch_id,
            batches.uploaded_at AS exported_at,
            batches.object_key AS export_object_key,
            settings.settings_json
        FROM device_settings_snapshots settings
        LEFT JOIN export_batch_records records
          ON records.record_type = 'device_settings'
         AND records.record_id = settings.id
        LEFT JOIN export_batches batches
          ON batches.batch_id = records.batch_id
        """
    )
    connection.execute(
        """
        CREATE VIEW IF NOT EXISTS weather_snapshots_export_status AS
        SELECT
            weather.id,
            weather.captured_at,
            weather.provider,
            weather.location_label,
            weather.temperature_c,
            weather.apparent_temperature_c,
            weather.relative_humidity_percent,
            weather.cloud_cover_percent,
            weather.precipitation_mm,
            weather.rain_mm,
            weather.snowfall_cm,
            weather.wind_speed_kmh,
            weather.wind_gust_kmh,
            weather.wind_direction_deg,
            weather.weather_code,
            weather.shortwave_radiation_w_m2,
            weather.direct_radiation_w_m2,
            weather.diffuse_radiation_w_m2,
            weather.direct_normal_irradiance_w_m2,
            weather.sunrise,
            weather.sunset,
            weather.moon_phase,
            weather.aurora_probability_percent,
            weather.aurora_forecast_time,
            records.batch_id AS export_batch_id,
            batches.uploaded_at AS exported_at,
            batches.object_key AS export_object_key,
            weather.raw_json
        FROM weather_snapshots weather
        LEFT JOIN export_batch_records records
          ON records.record_type = 'weather_snapshot'
         AND records.record_id = weather.id
        LEFT JOIN export_batches batches
          ON batches.batch_id = records.batch_id
        """
    )


def record_supervisor_snapshot(
    connection: sqlite3.Connection,
    snapshot: SupervisorSnapshot,
    load_summary: LoadSummary | None = None,
) -> None:
    payload = snapshot_api_payload(snapshot, load_summary=load_summary, now=snapshot.captured_at)
    connection.execute(
        """
        INSERT INTO supervisor_snapshots (captured_at, ok, status, snapshot_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            snapshot.captured_at.isoformat(),
            1 if snapshot.ok else 0,
            snapshot.status_text,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
    )


def record_device_settings_snapshot(
    connection: sqlite3.Connection,
    captured_at: datetime,
    device_id: str,
    settings_hash: str,
    reason: str,
    settings_json: str,
) -> None:
    connection.execute(
        """
        INSERT INTO device_settings_snapshots (
            captured_at, device_id, settings_hash, reason, settings_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (captured_at.isoformat(), device_id, settings_hash, reason, settings_json),
    )


def record_weather_snapshot(connection: sqlite3.Connection, report: WeatherReport) -> None:
    current = report.data.get("current") or {}
    daily = report.data.get("daily") or {}
    aurora = report.data.get("aurora") or {}
    existing = connection.execute(
        """
        SELECT 1
        FROM weather_snapshots
        WHERE captured_at = ?
          AND provider = ?
          AND location_label = ?
        LIMIT 1
        """,
        (report.fetched_at.isoformat(), "open-meteo", report.label),
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        """
        INSERT INTO weather_snapshots (
            captured_at, provider, location_label, temperature_c, apparent_temperature_c,
            relative_humidity_percent, cloud_cover_percent, precipitation_mm, rain_mm,
            snowfall_cm, wind_speed_kmh, wind_gust_kmh, wind_direction_deg, weather_code,
            shortwave_radiation_w_m2, direct_radiation_w_m2, diffuse_radiation_w_m2,
            direct_normal_irradiance_w_m2, sunrise, sunset, moon_phase,
            aurora_probability_percent, aurora_forecast_time, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report.fetched_at.isoformat(),
            "open-meteo",
            report.label,
            _number(current.get("temperature_2m")),
            _number(current.get("apparent_temperature")),
            _number(current.get("relative_humidity_2m")),
            _number(current.get("cloud_cover")),
            _number(current.get("precipitation")),
            _number(current.get("rain")),
            _number(current.get("snowfall")),
            _number(current.get("wind_speed_10m")),
            _number(current.get("wind_gusts_10m")),
            _number(current.get("wind_direction_10m")),
            _integer(current.get("weather_code")),
            _number(current.get("shortwave_radiation")),
            _number(current.get("direct_radiation")),
            _number(current.get("diffuse_radiation")),
            _number(current.get("direct_normal_irradiance")),
            _first(daily.get("sunrise")),
            _first(daily.get("sunset")),
            _number(_first(daily.get("moon_phase"))),
            _number(aurora.get("probability_percent") if isinstance(aurora, dict) else None),
            aurora.get("forecast_time") if isinstance(aurora, dict) else None,
            json.dumps(report.data, sort_keys=True, separators=(",", ":")),
        ),
    )


def classic_settings_payload(settings) -> dict:
    return {
        "battery_current_limit_a": settings.battery_current_limit_a,
        "absorb_voltage_v": settings.absorb_voltage_v,
        "float_voltage_v": settings.float_voltage_v,
        "equalize_voltage_v": settings.equalize_voltage_v,
        "sliding_current_limit_a": settings.sliding_current_limit_a,
        "absorb_time_s": settings.absorb_time_s,
        "max_temp_comp_voltage_v": settings.max_temp_comp_voltage_v,
        "min_temp_comp_voltage_v": settings.min_temp_comp_voltage_v,
        "temp_comp_mv_per_c_cell": settings.temp_comp_mv_per_c_cell,
        "mppt_mode_raw": settings.mppt_mode_raw,
        "aux_function_word": settings.aux_function_word,
    }


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first(values):
    if isinstance(values, list) and values:
        return values[0]
    return None


def _ensure_weather_columns(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(weather_snapshots)")}
    for name, definition in [
        ("sunrise", "TEXT"),
        ("sunset", "TEXT"),
        ("moon_phase", "REAL"),
        ("aurora_probability_percent", "REAL"),
        ("aurora_forecast_time", "TEXT"),
    ]:
        if name not in columns:
            connection.execute(f"ALTER TABLE weather_snapshots ADD COLUMN {name} {definition}")


def _ensure_metric_export_columns(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(metric_samples)")}
    for name, definition in [
        ("sample_id", "TEXT"),
        ("exported_at", "TEXT"),
        ("export_batch_id", "TEXT"),
    ]:
        if name not in columns:
            connection.execute(f"ALTER TABLE metric_samples ADD COLUMN {name} {definition}")


def _backfill_sample_ids(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE metric_samples
        SET sample_id = 'legacy-local-row-' || id
        WHERE sample_id IS NULL
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
        if extended.min_cell_pack_number is not None:
            yield MetricSample(captured_at, source, "min_cell_pack_number", value=float(extended.min_cell_pack_number))
        if extended.min_cell_number is not None:
            yield MetricSample(captured_at, source, "min_cell_number", value=float(extended.min_cell_number))
        if extended.min_cell_location_text() is not None:
            yield MetricSample(captured_at, source, "min_cell_location", text=extended.min_cell_location_text())
        if extended.max_cell_pack_number is not None:
            yield MetricSample(captured_at, source, "max_cell_pack_number", value=float(extended.max_cell_pack_number))
        if extended.max_cell_number is not None:
            yield MetricSample(captured_at, source, "max_cell_number", value=float(extended.max_cell_number))
        if extended.max_cell_location_text() is not None:
            yield MetricSample(captured_at, source, "max_cell_location", text=extended.max_cell_location_text())
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
