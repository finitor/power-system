"""Best-effort flat time-series telemetry store (decision 0003).

One canonical local model: scalar telemetry goes to the flat
``samples`` EAV table, irregular events to the hash-keyed
``events`` table. Both carry a content-hashed identity column so merging
two stores is an idempotent ``INSERT OR IGNORE`` union.

Logging is strictly isolated from supervision: every public method on
:class:`MetricRecorder` catches all errors, and a store that fails to
open is moved aside and recreated rather than retried into. Reads
degrade to "no data" on any failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Callable, Iterable

from .load import LoadSample, LoadSummary
from .supervisor import SupervisorSnapshot
from .weather import WeatherReport


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


@dataclass(frozen=True)
class TelemetryEvent:
    captured_at: datetime
    source: str
    event: str
    detail: dict | None = None

    def event_id(self) -> str:
        payload = {
            "captured_at": self.captured_at.isoformat(),
            "source": self.source,
            "event": self.event,
            "detail": self.detail or {},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class MetricRecorder:
    def __init__(
        self,
        path: str | None = "data/metrics.sqlite",
        snapshot_interval_s: float = 60,
    ) -> None:
        self.path = Path(path) if path else None
        self._initialized = False
        self.snapshot_interval = timedelta(seconds=snapshot_interval_s)
        self._last_snapshot_recorded_at: datetime | None = None
        self._last_weather_recorded_at: datetime | None = None

    def record_snapshot(
        self,
        snapshot: SupervisorSnapshot,
        load_summary: LoadSummary | None = None,
    ) -> None:
        if self.path is None:
            return
        if not self._should_record_snapshot(snapshot.captured_at):
            return
        samples = list(snapshot_metric_samples(snapshot, load_summary))
        if self._write(lambda connection: _insert_samples(connection, samples)):
            self._last_snapshot_recorded_at = snapshot.captured_at

    def record_weather(self, report: WeatherReport | None) -> None:
        if self.path is None or report is None or report.stale or not report.data:
            return
        if self._last_weather_recorded_at == report.fetched_at:
            return
        samples = list(weather_metric_samples(report))
        if self._write(lambda connection: _insert_samples(connection, samples)):
            self._last_weather_recorded_at = report.fetched_at

    def record_event(self, event: TelemetryEvent) -> None:
        if self.path is None:
            return
        self._write(lambda connection: _insert_events(connection, [event]))

    def recent_load_samples(
        self,
        now: datetime | None = None,
        window: timedelta = timedelta(hours=24),
    ) -> list[LoadSample]:
        """Reconstruct recent load samples to seed the in-memory buffer."""
        if self.path is None or not self.path.exists():
            return []
        reference = (now or datetime.now().astimezone()).astimezone()
        cutoff = (reference - window).isoformat()

        def query(connection: sqlite3.Connection) -> list[LoadSample]:
            rows = connection.execute(
                """
                SELECT captured_at, metric, value
                FROM samples
                WHERE source = 'load'
                  AND metric IN ('current', 'power')
                  AND value IS NOT NULL
                  AND captured_at >= ?
                ORDER BY captured_at
                """,
                (cutoff,),
            ).fetchall()
            by_time: dict[str, dict[str, float]] = {}
            for captured_at_text, metric, value in rows:
                by_time.setdefault(captured_at_text, {})[metric] = value
            samples: list[LoadSample] = []
            for captured_at_text, values in by_time.items():
                if "current" not in values or "power" not in values:
                    continue
                samples.append(
                    LoadSample(
                        captured_at=datetime.fromisoformat(captured_at_text),
                        current_a=values["current"],
                        power_w=round(values["power"]),
                    )
                )
            samples.sort(key=lambda sample: sample.captured_at)
            return samples

        return self._read(query, [])

    def midnight_soc_percent(self, day: date) -> int | None:
        """First battery SOC recorded within 5 minutes of local midnight."""
        if self.path is None or not self.path.exists():
            return None

        def query(connection: sqlite3.Connection) -> int | None:
            rows = connection.execute(
                """
                SELECT captured_at, value
                FROM samples
                WHERE source = 'battery'
                  AND metric = 'soc'
                  AND value IS NOT NULL
                  AND captured_at LIKE ?
                ORDER BY captured_at
                LIMIT 20
                """,
                (f"{day.isoformat()}T00:0%",),
            ).fetchall()
            for captured_at_text, value in rows:
                captured_at = datetime.fromisoformat(captured_at_text)
                if captured_at.minute * 60 + captured_at.second <= 300:
                    return round(value)
            return None

        return self._read(query, None)

    def _should_record_snapshot(self, captured_at: datetime) -> bool:
        if self._last_snapshot_recorded_at is None:
            return True
        return captured_at - self._last_snapshot_recorded_at >= self.snapshot_interval

    def _write(self, operation: Callable[[sqlite3.Connection], None]) -> bool:
        try:
            self._write_once(operation)
            return True
        except sqlite3.DatabaseError as exc:
            self._discard_store(exc)
        except Exception as exc:  # noqa: BLE001 - logging must never disrupt supervision.
            print(f"Telemetry store write failed: {exc}", file=sys.stderr)
            return False
        try:
            self._write_once(operation)
            return True
        except Exception as exc:  # noqa: BLE001 - logging must never disrupt supervision.
            print(f"Telemetry store write failed after recreate: {exc}", file=sys.stderr)
            return False

    def _write_once(self, operation: Callable[[sqlite3.Connection], None]) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=60)
        try:
            connection.execute("PRAGMA busy_timeout = 60000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            if not self._initialized:
                initialize_metrics_db(connection)
                self._initialized = True
            with connection:
                operation(connection)
        finally:
            connection.close()

    def _read(self, query: Callable[[sqlite3.Connection], object], default):
        assert self.path is not None
        try:
            connection = sqlite3.connect(self.path, timeout=60)
            try:
                connection.execute("PRAGMA busy_timeout = 60000")
                return query(connection)
            finally:
                connection.close()
        except Exception:  # noqa: BLE001 - reads degrade to "no data".
            return default

    def _discard_store(self, cause: Exception) -> None:
        """Move a store that fails to open/write aside so a fresh one can be created."""
        assert self.path is not None
        self._initialized = False
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
        print(
            f"Telemetry store unusable ({cause}); recreating {self.path}",
            file=sys.stderr,
        )
        for suffix in ("", "-wal", "-shm"):
            sidecar = Path(f"{self.path}{suffix}")
            if not sidecar.exists():
                continue
            try:
                sidecar.rename(f"{sidecar}.corrupt-{stamp}")
            except OSError:
                try:
                    sidecar.unlink()
                except OSError:
                    pass


def initialize_metrics_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS samples (
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
        CREATE UNIQUE INDEX IF NOT EXISTS samples_sample_id_idx
        ON samples (sample_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS samples_metric_time_idx
        ON samples (source, metric, captured_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS samples_time_idx
        ON samples (captured_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS samples_export_idx
        ON samples (exported_at, id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            event_id TEXT,
            captured_at TEXT NOT NULL,
            source TEXT NOT NULL,
            event TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            exported_at TEXT,
            export_batch_id TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS events_event_id_idx
        ON events (event_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS events_source_time_idx
        ON events (source, event, captured_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS events_time_idx
        ON events (captured_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS events_export_idx
        ON events (exported_at, id)
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


def _insert_samples(connection: sqlite3.Connection, samples: list[MetricSample]) -> None:
    connection.executemany(
        """
        INSERT OR IGNORE INTO samples (
            sample_id, captured_at, source, metric, value, text, unit, tags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                sample.sample_id(),
                sample.captured_at.isoformat(),
                sample.source,
                sample.metric,
                sample.value,
                sample.text,
                sample.unit,
                json.dumps(sample.tags or {}, sort_keys=True, separators=(",", ":")),
            )
            for sample in samples
        ],
    )


def _insert_events(connection: sqlite3.Connection, events: list[TelemetryEvent]) -> None:
    connection.executemany(
        """
        INSERT OR IGNORE INTO events (
            event_id, captured_at, source, event, detail_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                event.event_id(),
                event.captured_at.isoformat(),
                event.source,
                event.event,
                json.dumps(event.detail or {}, sort_keys=True, separators=(",", ":")),
            )
            for event in events
        ],
    )


def _ensure_metric_export_columns(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(samples)")}
    for name, definition in [
        ("sample_id", "TEXT"),
        ("exported_at", "TEXT"),
        ("export_batch_id", "TEXT"),
    ]:
        if name not in columns:
            connection.execute(f"ALTER TABLE samples ADD COLUMN {name} {definition}")


def _backfill_sample_ids(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE samples
        SET sample_id = 'legacy-local-row-' || id
        WHERE sample_id IS NULL
        """
    )


def merge_metric_stores(source_path: str | Path, dest_path: str | Path) -> tuple[int, int]:
    """Union the source store into the destination store; idempotent.

    Row identity is the content hash (sample_id/event_id, UNIQUE), so this
    is safe to re-run and order-independent. Returns the number of sample
    and event rows newly inserted. Used to sync the SD fallback store back
    onto the SSD after a removal gap.
    """
    connection = sqlite3.connect(dest_path, timeout=60)
    try:
        connection.execute("PRAGMA busy_timeout = 60000")
        initialize_metrics_db(connection)
        connection.execute("ATTACH DATABASE ? AS other", (str(source_path),))
        with connection:
            samples = connection.execute(
                """
                INSERT OR IGNORE INTO main.samples (
                    sample_id, captured_at, source, metric, value, text, unit, tags_json
                )
                SELECT sample_id, captured_at, source, metric, value, text, unit, tags_json
                FROM other.samples
                """
            ).rowcount
            events = connection.execute(
                """
                INSERT OR IGNORE INTO main.events (
                    event_id, captured_at, source, event, detail_json
                )
                SELECT event_id, captured_at, source, event, detail_json
                FROM other.events
                """
            ).rowcount
        return samples, events
    finally:
        connection.close()


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
    if snapshot.epever is not None:
        yield from _epever_samples(snapshot.epever.captured_at.astimezone(), snapshot.epever)
    if snapshot.epever_settings is not None:
        yield from _epever_settings_samples(snapshot.epever_settings.captured_at.astimezone(), snapshot.epever_settings)
    if snapshot.battery is not None:
        yield from _battery_samples(captured_at, snapshot.battery)
    if snapshot.battery_can_health is not None:
        yield from _battery_can_health_samples(captured_at, snapshot.battery_can_health)
    if snapshot.magnum is not None:
        yield from _magnum_samples(snapshot.magnum.captured_at.astimezone(), snapshot.magnum)
    if snapshot.ambient is not None:
        yield from _ambient_samples(snapshot.ambient.captured_at.astimezone(), snapshot.ambient)


def weather_metric_samples(report: WeatherReport) -> Iterable[MetricSample]:
    captured_at = report.fetched_at.astimezone()
    source = "weather"
    current = report.data.get("current") or {}
    daily = report.data.get("daily") or {}
    aurora = report.data.get("aurora")
    if not isinstance(aurora, dict):
        aurora = {}

    for metric, key, unit in [
        ("temperature", "temperature_2m", "C"),
        ("apparent_temperature", "apparent_temperature", "C"),
        ("relative_humidity", "relative_humidity_2m", "%"),
        ("cloud_cover", "cloud_cover", "%"),
        ("precipitation", "precipitation", "mm"),
        ("rain", "rain", "mm"),
        ("snowfall", "snowfall", "cm"),
        ("wind_speed", "wind_speed_10m", "km/h"),
        ("wind_gust", "wind_gusts_10m", "km/h"),
        ("wind_direction", "wind_direction_10m", "deg"),
        ("weather_code", "weather_code", None),
        ("shortwave_radiation", "shortwave_radiation", "W/m2"),
        ("direct_radiation", "direct_radiation", "W/m2"),
        ("diffuse_radiation", "diffuse_radiation", "W/m2"),
        ("direct_normal_irradiance", "direct_normal_irradiance", "W/m2"),
    ]:
        value = _number(current.get(key))
        if value is not None:
            yield MetricSample(captured_at, source, metric, value=value, unit=unit)

    sunrise = _first(daily.get("sunrise"))
    if sunrise is not None:
        yield MetricSample(captured_at, source, "sunrise", text=str(sunrise))
    sunset = _first(daily.get("sunset"))
    if sunset is not None:
        yield MetricSample(captured_at, source, "sunset", text=str(sunset))
    moon_phase = _number(_first(daily.get("moon_phase")))
    if moon_phase is not None:
        yield MetricSample(captured_at, source, "moon_phase", value=moon_phase)
    aurora_probability = _number(aurora.get("probability_percent"))
    if aurora_probability is not None:
        yield MetricSample(captured_at, source, "aurora_probability", value=aurora_probability, unit="%")
    aurora_forecast_time = aurora.get("forecast_time")
    if aurora_forecast_time is not None:
        yield MetricSample(captured_at, source, "aurora_forecast_time", text=str(aurora_forecast_time))


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
    yield MetricSample(captured_at, source, "charge_stage", text=classic.canonical_stage.value)
    yield MetricSample(captured_at, source, "charge_stage_vendor", text=classic.charge_stage)
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


def _epever_samples(captured_at: datetime, epever) -> Iterable[MetricSample]:
    source = "epever.1"
    yield MetricSample(captured_at, source, "battery_voltage", value=epever.battery_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "pv_voltage", value=epever.pv_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "battery_current", value=epever.battery_current_a, unit="A")
    yield MetricSample(captured_at, source, "pv_current", value=epever.pv_current_a, unit="A")
    yield MetricSample(captured_at, source, "battery_power", value=float(epever.battery_power_w), unit="W")
    yield MetricSample(captured_at, source, "pv_power", value=float(epever.pv_power_w), unit="W")
    if epever.battery_soc_percent is not None:
        yield MetricSample(captured_at, source, "battery_soc", value=float(epever.battery_soc_percent), unit="%")
    if epever.battery_temp_c is not None:
        yield MetricSample(captured_at, source, "battery_temperature", value=epever.battery_temp_c, unit="C")
    if epever.device_temp_c is not None:
        yield MetricSample(captured_at, source, "device_temperature", value=epever.device_temp_c, unit="C")
    yield MetricSample(captured_at, source, "status_raw", value=float(epever.status_raw))
    yield MetricSample(captured_at, source, "charge_stage", text=epever.canonical_stage.value)
    yield MetricSample(captured_at, source, "charge_stage_vendor", text=epever.charging_status)


def _epever_settings_samples(captured_at: datetime, settings) -> Iterable[MetricSample]:
    source = "epever.1.settings"
    yield MetricSample(captured_at, source, "battery_type_code", value=float(settings.battery_type_code))
    yield MetricSample(captured_at, source, "battery_type", text=settings.battery_type)
    yield MetricSample(captured_at, source, "battery_capacity", value=float(settings.battery_capacity_ah), unit="Ah")
    yield MetricSample(captured_at, source, "temp_comp", value=float(settings.temperature_compensation_mv_per_c_cell), unit="mV/C/cell")
    yield MetricSample(captured_at, source, "over_voltage_disconnect", value=settings.over_voltage_disconnect_v, unit="V")
    yield MetricSample(captured_at, source, "charging_limit_voltage", value=settings.charging_limit_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "over_voltage_reconnect", value=settings.over_voltage_reconnect_v, unit="V")
    yield MetricSample(captured_at, source, "equalize_voltage", value=settings.equalize_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "boost_voltage", value=settings.boost_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "float_voltage", value=settings.float_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "boost_reconnect_voltage", value=settings.boost_reconnect_voltage_v, unit="V")
    yield MetricSample(captured_at, source, "low_voltage_reconnect", value=settings.low_voltage_reconnect_v, unit="V")
    yield MetricSample(captured_at, source, "under_voltage_recover", value=settings.under_voltage_recover_v, unit="V")
    yield MetricSample(captured_at, source, "under_voltage_warning", value=settings.under_voltage_warning_v, unit="V")
    yield MetricSample(captured_at, source, "low_voltage_disconnect", value=settings.low_voltage_disconnect_v, unit="V")
    yield MetricSample(captured_at, source, "discharging_limit_voltage", value=settings.discharging_limit_voltage_v, unit="V")
    if settings.max_charging_current_a is not None:
        yield MetricSample(captured_at, source, "max_charging_current", value=settings.max_charging_current_a, unit="A")


def _magnum_samples(captured_at: datetime, magnum) -> Iterable[MetricSample]:
    source = "magnum"
    yield MetricSample(captured_at, source, "dc_voltage", value=magnum.dc_volts, unit="V")
    yield MetricSample(captured_at, source, "dc_current", value=float(magnum.dc_amps), unit="A")
    yield MetricSample(captured_at, source, "dc_power", value=float(magnum.dc_power_w), unit="W")
    yield MetricSample(captured_at, source, "ac_voltage_out", value=float(magnum.ac_volts_out), unit="V")
    yield MetricSample(captured_at, source, "ac_voltage_in", value=float(magnum.ac_volts_in), unit="V")
    if magnum.ac_amps_in is not None:
        yield MetricSample(captured_at, source, "ac_current_in", value=float(magnum.ac_amps_in), unit="A")
    if magnum.ac_amps_out is not None:
        yield MetricSample(captured_at, source, "ac_current_out", value=float(magnum.ac_amps_out), unit="A")
    if magnum.ac_power_w is not None:
        yield MetricSample(captured_at, source, "ac_power", value=float(magnum.ac_power_w), unit="W")
    if magnum.ac_freq_hz is not None:
        yield MetricSample(captured_at, source, "ac_frequency", value=magnum.ac_freq_hz, unit="Hz")
    yield MetricSample(captured_at, source, "inverter_on", value=1.0 if magnum.inverter_on else 0.0)
    yield MetricSample(captured_at, source, "charger_on", value=1.0 if magnum.charger_on else 0.0)
    yield MetricSample(captured_at, source, "status", text=magnum.status_name)
    yield MetricSample(captured_at, source, "fault", text=magnum.fault_name)
    yield MetricSample(captured_at, source, "battery_temperature", value=float(magnum.battery_temp_c), unit="C")
    yield MetricSample(captured_at, source, "transformer_temperature", value=float(magnum.transformer_temp_c), unit="C")
    yield MetricSample(captured_at, source, "fet_temperature", value=float(magnum.fet_temp_c), unit="C")
    if magnum.absorb_v is not None:
        yield MetricSample(captured_at, source, "absorb_voltage", value=magnum.absorb_v, unit="V")
    if magnum.float_v is not None:
        yield MetricSample(captured_at, source, "float_voltage", value=magnum.float_v, unit="V")
    if magnum.absorb_time_hr is not None:
        yield MetricSample(captured_at, source, "absorb_time", value=magnum.absorb_time_hr, unit="h")
    if magnum.shore_amps is not None:
        yield MetricSample(captured_at, source, "shore_current_limit", value=float(magnum.shore_amps), unit="A")
    if magnum.charger_amps_pct is not None:
        yield MetricSample(captured_at, source, "charger_current_pct", value=float(magnum.charger_amps_pct), unit="%")


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


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(values):
    if isinstance(values, list) and values:
        return values[0]
    return None


def _hours_text_value(text: str | None) -> float | None:
    if text is None or not text.endswith("h"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None
