"""Best-effort flat time-series telemetry store (decision 0003).

One canonical local storage model: scalar telemetry goes to the flat
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
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Callable, Iterable

from .load import LoadSample, LoadSummary
from .supervisor import SupervisorSnapshot
from .weather import WeatherReport, weather_api_payload


def utc_timestamp_text(value: datetime) -> str:
    """Canonical durable timestamp text: aware UTC ISO 8601.

    Naive datetimes are treated the way Python's ``astimezone`` treats them:
    local wall time. Runtime readers should produce aware datetimes; the naive
    fallback keeps tests/tools from writing ambiguous strings silently.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _local_time_on_day(day: date) -> datetime:
    # Build a naive local wall-clock time and let astimezone attach the system
    # local zone for that date. This preserves local-day semantics across DST
    # without hard-coding a fixed offset.
    return datetime.combine(day, time.min).replace(tzinfo=None).astimezone()


def _local_window_utc_text(day: date, duration: timedelta) -> tuple[str, str]:
    start = _local_time_on_day(day)
    end = start + duration
    return utc_timestamp_text(start), utc_timestamp_text(end)


def local_day_utc_bounds(day: date) -> tuple[str, str]:
    """UTC timestamp-text bounds ``[start, end)`` spanning the local day.

    ``captured_at`` is stored in canonical UTC, but "a day" for this off-grid
    site means a local calendar day (the Classic's daily counters reset at
    local midnight). Convert both local-midnight boundaries to UTC text so a
    caller can select a local day with a direct lexical comparison against
    stored values. Computing the end from the next day's local midnight (rather
    than start + 24h) keeps the window correct across DST transitions.
    """
    start = _local_time_on_day(day)
    end = _local_time_on_day(day + timedelta(days=1))
    return utc_timestamp_text(start), utc_timestamp_text(end)


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
            "captured_at": utc_timestamp_text(self.captured_at),
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
            "captured_at": utc_timestamp_text(self.captured_at),
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
        mountpoint: str | None = None,
        fallback_path: str | None = None,
    ) -> None:
        """Best-effort recorder with an optional fallback store.

        `mountpoint` guards against the shadowed-directory trap: if the
        primary store lives on a removable mount, writes while it is
        unmounted would silently land in the directory shadowed beneath it
        and become invisible on remount. When set, the primary is used only
        while `mountpoint` is actually mounted; otherwise (or when a primary
        write fails) writes go to `fallback_path`, and the fallback is
        merged back and removed after the next successful primary write.
        """
        self.path = Path(path) if path else None
        self.mountpoint = mountpoint
        self.fallback_path = Path(fallback_path) if fallback_path else None
        self._initialized_paths: set[Path] = set()
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
        store = self._active_path()
        if store is None or not store.exists():
            return []
        reference = now or datetime.now(timezone.utc)
        cutoff = utc_timestamp_text(reference - window)

        def query(connection: sqlite3.Connection) -> list[LoadSample]:
            rows = connection.execute(
                """
                SELECT captured_at, metric, value
                FROM samples
                WHERE source = 'load'
                  AND metric IN ('current', 'power')
                  AND value IS NOT NULL
                  AND julianday(captured_at) >= julianday(?)
                ORDER BY julianday(captured_at), captured_at
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
                        captured_at=parse_timestamp(captured_at_text),
                        current_a=values["current"],
                        power_w=round(values["power"]),
                    )
                )
            samples.sort(key=lambda sample: sample.captured_at)
            return samples

        return self._read(query, [])

    def recent_metric_values(
        self,
        source: str,
        metric: str,
        now: datetime | None = None,
        window: timedelta = timedelta(hours=3),
    ) -> list[tuple[datetime, float]]:
        """Return recent numeric values for seeding a rolling device average."""
        store = self._active_path()
        if store is None or not store.exists():
            return []
        reference = now or datetime.now(timezone.utc)
        cutoff = utc_timestamp_text(reference - window)

        def query(connection: sqlite3.Connection) -> list[tuple[datetime, float]]:
            rows = connection.execute(
                """
                SELECT captured_at, value
                FROM samples
                WHERE source = ? AND metric = ? AND value IS NOT NULL
                  AND julianday(captured_at) >= julianday(?)
                ORDER BY julianday(captured_at), captured_at
                """,
                (source, metric, cutoff),
            ).fetchall()
            return [(parse_timestamp(captured_at), float(value)) for captured_at, value in rows]

        return self._read(query, [])

    def midnight_soc_percent(self, day: date) -> int | None:
        """First battery SOC recorded within 5 minutes of local midnight."""
        store = self._active_path()
        if store is None or not store.exists():
            return None

        def query(connection: sqlite3.Connection) -> int | None:
            start, end = _local_window_utc_text(day, timedelta(minutes=5))
            rows = connection.execute(
                """
                SELECT captured_at, value
                FROM samples
                WHERE source = 'battery'
                  AND metric = 'soc'
                  AND value IS NOT NULL
                  AND julianday(captured_at) >= julianday(?)
                  AND julianday(captured_at) < julianday(?)
                ORDER BY julianday(captured_at), captured_at
                LIMIT 20
                """,
                (start, end),
            ).fetchall()
            for captured_at_text, value in rows:
                captured_at = parse_timestamp(captured_at_text).astimezone()
                if captured_at.minute * 60 + captured_at.second <= 300:
                    return round(value)
            return None

        return self._read(query, None)

    def midnight_metric_value(self, source: str, metric: str, day: date) -> float | None:
        """First value of source/metric recorded within 5 minutes of local
        midnight on ``day`` -- used to difference a monotonic lifetime counter
        into a since-midnight delta."""
        store = self._active_path()
        if store is None or not store.exists():
            return None

        def query(connection: sqlite3.Connection) -> float | None:
            start, end = _local_window_utc_text(day, timedelta(minutes=5))
            rows = connection.execute(
                """
                SELECT captured_at, value
                FROM samples
                WHERE source = ?
                  AND metric = ?
                  AND value IS NOT NULL
                  AND julianday(captured_at) >= julianday(?)
                  AND julianday(captured_at) < julianday(?)
                ORDER BY julianday(captured_at), captured_at
                LIMIT 20
                """,
                (source, metric, start, end),
            ).fetchall()
            for captured_at_text, value in rows:
                captured_at = parse_timestamp(captured_at_text).astimezone()
                if captured_at.minute * 60 + captured_at.second <= 300:
                    return value
            return None

        return self._read(query, None)

    def _should_record_snapshot(self, captured_at: datetime) -> bool:
        if self._last_snapshot_recorded_at is None:
            return True
        return captured_at - self._last_snapshot_recorded_at >= self.snapshot_interval

    def _primary_mounted(self) -> bool:
        if self.mountpoint is None:
            return True
        return os.path.ismount(self.mountpoint)

    def _active_path(self) -> Path | None:
        """The store reads should come from right now."""
        if self._primary_mounted():
            return self.path
        return self.fallback_path or self.path

    def _write(self, operation: Callable[[sqlite3.Connection], None]) -> bool:
        if self._primary_mounted():
            if self._try_store(self.path, operation):
                self._merge_fallback_if_present()
                return True
        if self.fallback_path is None:
            return False
        return self._try_store(self.fallback_path, operation)

    def _try_store(self, path: Path, operation: Callable[[sqlite3.Connection], None]) -> bool:
        try:
            self._write_once(path, operation)
            return True
        except sqlite3.OperationalError as exc:
            # I/O errors and open failures (e.g. a yanked USB device) are
            # not corruption; recreating would not help, falling back might.
            print(f"Telemetry store write failed ({path}): {exc}", file=sys.stderr)
            return False
        except sqlite3.DatabaseError as exc:
            self._discard_store(path, exc)
        except Exception as exc:  # noqa: BLE001 - logging must never disrupt supervision.
            print(f"Telemetry store write failed ({path}): {exc}", file=sys.stderr)
            return False
        try:
            self._write_once(path, operation)
            return True
        except Exception as exc:  # noqa: BLE001 - logging must never disrupt supervision.
            print(f"Telemetry store write failed after recreate ({path}): {exc}", file=sys.stderr)
            return False

    def _merge_fallback_if_present(self) -> None:
        """After a successful primary write, fold a fallback gap back in.

        The union is idempotent (content-hash ids), so a failure here just
        leaves the fallback in place to retry on the next tick.
        """
        if self.fallback_path is None or not self.fallback_path.exists():
            return
        assert self.path is not None
        try:
            merged_samples, merged_events = merge_metric_stores(self.fallback_path, self.path)
            self._initialized_paths.discard(self.fallback_path)
            for suffix in ("", "-wal", "-shm"):
                Path(f"{self.fallback_path}{suffix}").unlink(missing_ok=True)
            print(
                f"Telemetry fallback store merged back: +{merged_samples} samples, +{merged_events} events",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 - merge failures degrade logging only.
            print(f"Telemetry fallback merge failed (will retry): {exc}", file=sys.stderr)

    def _write_once(self, path: Path, operation: Callable[[sqlite3.Connection], None]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=60)
        try:
            connection.execute("PRAGMA busy_timeout = 60000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            if path not in self._initialized_paths:
                initialize_metrics_db(connection)
                self._initialized_paths.add(path)
            with connection:
                operation(connection)
        finally:
            connection.close()

    def _read(self, query: Callable[[sqlite3.Connection], object], default):
        store = self._active_path()
        assert store is not None
        try:
            connection = sqlite3.connect(store, timeout=60)
            try:
                connection.execute("PRAGMA busy_timeout = 60000")
                return query(connection)
            finally:
                connection.close()
        except Exception:  # noqa: BLE001 - reads degrade to "no data".
            return default

    def _discard_store(self, path: Path, cause: Exception) -> None:
        """Move a corrupt store aside so a fresh one can be created."""
        self._initialized_paths.discard(path)
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
        print(
            f"Telemetry store unusable ({cause}); recreating {path}",
            file=sys.stderr,
        )
        for suffix in ("", "-wal", "-shm"):
            sidecar = Path(f"{path}{suffix}")
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
                utc_timestamp_text(sample.captured_at),
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
                utc_timestamp_text(event.captured_at),
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
    captured_at = snapshot.captured_at.astimezone(timezone.utc)
    yield MetricSample(captured_at, "supervisor", "ok", value=1.0 if snapshot.ok else 0.0)
    yield MetricSample(captured_at, "supervisor", "error_count", value=float(len(snapshot.errors)))
    for index, error in enumerate(snapshot.errors):
        yield MetricSample(captured_at, "supervisor", "error", text=error, tags={"index": str(index)})
    yield MetricSample(captured_at, "supervisor", "status_condition_count", value=float(len(snapshot.status_conditions)))
    for index, condition in enumerate(snapshot.status_conditions):
        yield MetricSample(captured_at, "supervisor", "status_condition", text=condition, tags={"index": str(index)})

    # This heartbeat remains while a controller is user disabled; its
    # individual telemetry/settings samples below disappear with snapshot data.
    for controller, source in ((0, "classic.0"), (1, "epever.1")):
        enabled = snapshot.charge_controller_enabled.get(controller, True)
        yield MetricSample(captured_at, source, "user_enabled", value=1.0 if enabled else 0.0)

    if load_summary is not None:
        yield from _load_samples(captured_at, load_summary)
    if snapshot.classic is not None:
        yield from _classic_samples(snapshot.classic.captured_at.astimezone(timezone.utc), snapshot.classic)
    if snapshot.classic_settings is not None:
        yield from _classic_settings_samples(snapshot.classic_settings.captured_at.astimezone(timezone.utc), snapshot.classic_settings)
    if snapshot.epever is not None:
        yield from _epever_samples(snapshot.epever.captured_at.astimezone(timezone.utc), snapshot.epever)
    if snapshot.epever_settings is not None:
        yield from _epever_settings_samples(snapshot.epever_settings.captured_at.astimezone(timezone.utc), snapshot.epever_settings)
    if snapshot.battery is not None:
        yield from _battery_samples(captured_at, snapshot.battery)
    if snapshot.battery_can_health is not None:
        yield from _battery_can_health_samples(captured_at, snapshot.battery_can_health)
    if snapshot.magnum is not None:
        yield from _magnum_samples(snapshot.magnum.captured_at.astimezone(timezone.utc), snapshot.magnum)
    if snapshot.ambient is not None:
        yield from _ambient_samples(snapshot.ambient.captured_at.astimezone(timezone.utc), snapshot.ambient)
    for name, telemetry in snapshot.tasmota.items():
        yield from _tasmota_samples(name, telemetry)
    if snapshot.lan_reachable is not None:
        yield MetricSample(captured_at, "network", "lan_reachable", value=1.0 if snapshot.lan_reachable else 0.0)


def weather_metric_samples(report: WeatherReport) -> Iterable[MetricSample]:
    # Consume the normalized weather payload so provider field names live only
    # in weather.py; this maps the canonical schema onto stored metric names.
    payload = weather_api_payload(report)
    captured_at = report.fetched_at.astimezone(timezone.utc)
    source = "weather"
    current = payload.get("current") or {}
    wind = current.get("wind") or {}
    irradiance = current.get("irradiance") or {}
    condition = current.get("condition") or {}

    for metric, value, unit in [
        ("temperature", current.get("temperature_c"), "C"),
        ("apparent_temperature", current.get("apparent_temperature_c"), "C"),
        ("relative_humidity", current.get("humidity_pct"), "%"),
        ("cloud_cover", current.get("cloud_cover_pct"), "%"),
        ("precipitation", current.get("precipitation_mm"), "mm"),
        ("rain", current.get("rain_mm"), "mm"),
        ("snowfall", current.get("snowfall_cm"), "cm"),
        ("wind_speed", wind.get("speed_kmh"), "km/h"),
        ("wind_gust", wind.get("gust_kmh"), "km/h"),
        ("wind_direction", wind.get("direction_deg"), "deg"),
        ("weather_code", condition.get("code"), None),
        ("shortwave_radiation", irradiance.get("ghi_wm2"), "W/m2"),
        ("direct_radiation", irradiance.get("direct_wm2"), "W/m2"),
        ("diffuse_radiation", irradiance.get("diffuse_wm2"), "W/m2"),
        ("direct_normal_irradiance", irradiance.get("dni_wm2"), "W/m2"),
    ]:
        if value is not None:
            yield MetricSample(captured_at, source, metric, value=float(value), unit=unit)

    astronomy = payload.get("astronomy") or {}
    sunrise = astronomy.get("sunrise")
    if sunrise is not None:
        yield MetricSample(captured_at, source, "sunrise", text=str(sunrise))
    sunset = astronomy.get("sunset")
    if sunset is not None:
        yield MetricSample(captured_at, source, "sunset", text=str(sunset))
    moon = astronomy.get("moon") or {}
    if moon.get("phase") is not None:
        yield MetricSample(captured_at, source, "moon_phase", value=float(moon["phase"]))
    aurora = astronomy.get("aurora") or {}
    if aurora.get("probability_pct") is not None:
        yield MetricSample(captured_at, source, "aurora_probability", value=float(aurora["probability_pct"]), unit="%")
    if aurora.get("valid_at") is not None:
        yield MetricSample(captured_at, source, "aurora_forecast_time", text=str(aurora["valid_at"]))


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


def _tasmota_samples(name: str, telemetry) -> Iterable[MetricSample]:
    captured_at = telemetry.captured_at.astimezone(timezone.utc)
    source = f"tasmota.{name}"
    yield MetricSample(captured_at, source, "voltage", value=telemetry.voltage_v, unit="V")
    yield MetricSample(captured_at, source, "current", value=telemetry.current_a, unit="A")
    yield MetricSample(captured_at, source, "power", value=telemetry.power_w, unit="W")
    yield MetricSample(captured_at, source, "apparent_power", value=telemetry.apparent_power_va, unit="VA")
    yield MetricSample(captured_at, source, "reactive_power", value=telemetry.reactive_power_var, unit="var")
    yield MetricSample(captured_at, source, "power_factor", value=telemetry.power_factor)
    yield MetricSample(captured_at, source, "daily_energy", value=telemetry.energy_today_kwh, unit="kWh")
    yield MetricSample(captured_at, source, "yesterday_energy", value=telemetry.energy_yesterday_kwh, unit="kWh")
    yield MetricSample(captured_at, source, "lifetime_energy", value=telemetry.energy_total_kwh, unit="kWh")


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
    if epever.pcb_temp_c is not None:
        yield MetricSample(captured_at, source, "pcb_temperature", value=epever.pcb_temp_c, unit="C")
    yield MetricSample(captured_at, source, "status_raw", value=float(epever.status_raw))
    yield MetricSample(captured_at, source, "charge_stage", text=epever.canonical_stage.value)
    yield MetricSample(captured_at, source, "charge_stage_vendor", text=epever.charging_status)
    if epever.generated_today_kwh is not None:
        yield MetricSample(captured_at, source, "generated_today", value=epever.generated_today_kwh, unit="kWh")
    if epever.generated_total_kwh is not None:
        yield MetricSample(captured_at, source, "generated_total", value=epever.generated_total_kwh, unit="kWh")


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


def _hours_text_value(text: str | None) -> float | None:
    if text is None or not text.endswith("h"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None
