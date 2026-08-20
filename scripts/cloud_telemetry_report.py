#!/usr/bin/env python3
r"""Build a read-only Markdown performance report from cloud telemetry.

Times passed to ``--start`` and ``--end`` are inclusive and interpreted in the
site timezone unless they include an explicit UTC offset. A date by itself is
local midnight. The command materializes the requested B2 window once into a
local Parquet cache, then runs every report query against that cache so repeated
aggregations do not consume more object-store transactions.

Examples:

    .venv/bin/python scripts/cloud_telemetry_report.py \
      --start 2026-08-13 --end 2026-08-18T06:10:31 \
      --credentials-backup backups/pi-migration/offgrid-blueberry-20260613T154102Z.tar.gz

    # Reuse an existing cache; no cloud credentials or network access needed.
    .venv/bin/python scripts/cloud_telemetry_report.py \
      --start 2026-08-13 --end 2026-08-18T06:10:31 \
      --cache /private/tmp/offgrid-cloud-report-cache/<cache>.parquet

Credentials are read from B2_* (preferred) or S3_* environment variables. An
explicit ``--credentials-backup`` may instead name a Pi migration tarball that
contains ``etc/offgrid-power.env``. Secrets are used only to create an in-memory
DuckDB S3 secret and are never printed or written to the sample cache.
"""

from __future__ import annotations

import argparse
import os
import tarfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import duckdb
except ImportError as exc:  # pragma: no cover - exercised only without analysis deps
    raise SystemExit(
        "duckdb is required; install the project analysis extras with "
        '`.venv/bin/pip install -e ".[analysis]"`.'
    ) from exc


DEFAULT_TIMEZONE = "America/Toronto"
DEFAULT_PREFIX = "metrics"
MAX_INTEGRATION_GAP_SECONDS = 180.0

# Keep the cloud materialization narrow. Every report query after this point is
# local, so adding a metric here is the only change needed for a new summary.
REPORT_METRICS = (
    ("ambient", "temperature"),
    ("battery", "alarm_flags"),
    ("battery", "cell_voltage_delta"),
    ("battery", "charge_current_limit"),
    ("battery", "charge_enable"),
    ("battery", "current"),
    ("battery", "discharge_enable"),
    ("battery", "max_cell_temperature"),
    ("battery", "max_cell_voltage"),
    ("battery", "min_cell_temperature"),
    ("battery", "min_cell_voltage"),
    ("battery", "power"),
    ("battery", "protection_flags"),
    ("battery", "soc"),
    ("battery", "voltage"),
    ("battery.can", "ok"),
    ("battery.can", "status"),
    ("classic.0", "battery_power"),
    ("classic.0", "charge_stage"),
    ("classic.0", "daily_energy"),
    ("classic.0", "pv_voltage"),
    ("epever.1", "battery_power"),
    ("epever.1", "user_enabled"),
    ("load", "power"),
    ("magnum", "ac_frequency"),
    ("magnum", "ac_power"),
    ("magnum", "ac_voltage_in"),
    ("magnum", "ac_voltage_out"),
    ("magnum", "charger_on"),
    ("magnum", "fault"),
    ("magnum", "fet_temperature"),
    ("magnum", "inverter_on"),
    ("magnum", "status"),
    ("magnum", "transformer_temperature"),
    ("network", "lan_reachable"),
    ("supervisor", "error"),
    ("supervisor", "error_count"),
    ("supervisor", "ok"),
    ("supervisor", "status_condition"),
    ("tasmota.refrigeration", "power"),
)


@dataclass(frozen=True)
class CloudConfig:
    key_id: str
    secret: str
    bucket: str
    endpoint: str
    region: str
    prefix: str = DEFAULT_PREFIX


@dataclass(frozen=True)
class ReportWindow:
    requested_start: datetime
    requested_end: datetime
    available_start: datetime
    available_end: datetime
    timezone: ZoneInfo


def parse_local_datetime(value: str, zone: ZoneInfo) -> datetime:
    """Parse an ISO date/datetime, applying ``zone`` only when naive."""
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        if "T" not in normalized and " " not in normalized:
            parsed = datetime.combine(date.fromisoformat(normalized), time.min)
        else:
            parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Expected an ISO date or datetime, got {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed


def utc_partition_dates(start: datetime, end: datetime) -> list[date]:
    """UTC object partitions intersecting the inclusive ``start``/``end``."""
    if end < start:
        raise ValueError("end must not be earlier than start")
    current = start.astimezone(timezone.utc).date()
    final = end.astimezone(timezone.utc).date()
    partitions = []
    while current <= final:
        partitions.append(current)
        current += timedelta(days=1)
    return partitions


def parse_env_text(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_backup_environment(path: Path) -> dict[str, str]:
    with tarfile.open(path, "r:gz") as archive:
        matches = [
            member for member in archive.getmembers()
            if member.name.endswith("/etc/offgrid-power.env")
        ]
        if len(matches) != 1:
            raise SystemExit(
                f"Expected one etc/offgrid-power.env in {path}, found {len(matches)}"
            )
        source = archive.extractfile(matches[0])
        if source is None:
            raise SystemExit(f"Could not read {matches[0].name} from {path}")
        return parse_env_text(source.read().decode())


def _first(values: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if value:
            return value
    return None


def load_cloud_config(backup: Path | None = None) -> CloudConfig:
    values: dict[str, str] = {}
    if backup is not None:
        values.update(read_backup_environment(backup))
    # Explicit process environment wins over an old migration backup.
    values.update(os.environ)

    fields = {
        "key_id": _first(values, "B2_APPLICATION_KEY_ID", "S3_ACCESS_KEY_ID"),
        "secret": _first(values, "B2_APPLICATION_KEY", "S3_SECRET_ACCESS_KEY"),
        "bucket": _first(values, "B2_BUCKET", "S3_BUCKET"),
        "endpoint": _first(values, "B2_ENDPOINT_URL", "S3_ENDPOINT_URL"),
        "region": _first(values, "B2_REGION", "S3_REGION"),
        "prefix": _first(values, "B2_PREFIX", "S3_PREFIX") or DEFAULT_PREFIX,
    }
    missing = [name for name in ("key_id", "secret", "bucket", "endpoint", "region")
               if not fields[name]]
    if missing:
        raise SystemExit(
            "Missing cloud settings: " + ", ".join(missing) + ". Set B2_* or "
            "S3_* variables, or pass --credentials-backup."
        )
    return CloudConfig(**fields)  # type: ignore[arg-type]


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def cache_path_for(start: datetime, end: datetime, cache_dir: Path) -> Path:
    def stamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    return cache_dir / f"samples-{stamp(start)}-{stamp(end)}.parquet"


def _create_s3_secret(connection, config: CloudConfig) -> None:
    endpoint = config.endpoint.split("://", 1)[-1].rstrip("/")
    connection.execute("LOAD httpfs")
    connection.execute(
        "CREATE OR REPLACE SECRET cloud_report ("
        "TYPE s3, "
        f"KEY_ID {sql_string(config.key_id)}, "
        f"SECRET {sql_string(config.secret)}, "
        f"REGION {sql_string(config.region)}, "
        f"ENDPOINT {sql_string(endpoint)}, "
        "URL_STYLE 'path', USE_SSL true)"
    )


def _discover_sample_files(connection, config: CloudConfig,
                           start: datetime, end: datetime) -> list[str]:
    files: list[str] = []
    for partition in utc_partition_dates(start, end):
        pattern = (
            f"s3://{config.bucket}/{config.prefix}/samples/"
            f"date={partition.isoformat()}/*.parquet"
        )
        files.extend(row[0] for row in connection.execute(
            "SELECT file FROM glob(?) ORDER BY file", [pattern]
        ).fetchall())
    if not files:
        raise SystemExit("No cloud sample objects intersect the requested UTC dates.")
    return files


def materialize_cloud_samples(config: CloudConfig, start: datetime, end: datetime,
                              cache: Path) -> None:
    """Download one filtered pass of the cloud window and atomically cache it."""
    connection = duckdb.connect()
    _create_s3_secret(connection, config)
    files = _discover_sample_files(connection, config, start, end)
    cache.parent.mkdir(parents=True, exist_ok=True)
    partial = cache.with_suffix(cache.suffix + ".partial")
    partial.unlink(missing_ok=True)

    metric_filter = ", ".join(
        f"({sql_string(source)}, {sql_string(metric)})"
        for source, metric in REPORT_METRICS
    )
    start_utc = start.astimezone(timezone.utc).isoformat()
    end_utc = end.astimezone(timezone.utc).isoformat()
    try:
        connection.execute(
            "COPY ("
            "SELECT record_id, captured_at, source, metric, value, text, unit "
            "FROM read_parquet(?, union_by_name=true, hive_partitioning=true) "
            f"WHERE captured_at >= {sql_string(start_utc)} "
            f"AND captured_at <= {sql_string(end_utc)} "
            f"AND (source, metric) IN ({metric_filter})"
            f") TO {sql_string(partial)} (FORMAT PARQUET, COMPRESSION ZSTD)",
            [files],
        )
        if not partial.exists() or partial.stat().st_size == 0:
            raise SystemExit("Cloud query returned no reportable samples.")
        os.replace(partial, cache)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        connection.close()


def _fmt(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _fmt_local(value: datetime, zone: ZoneInfo) -> str:
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S %Z")


def _is_complete_day(day: date, window: ReportWindow) -> bool:
    start = datetime.combine(day, time.min, window.timezone)
    finish = datetime.combine(day + timedelta(days=1), time.min, window.timezone)
    tolerance = timedelta(minutes=5)
    return (
        window.requested_start <= start
        and window.requested_end >= finish - tolerance
        and window.available_start <= start + tolerance
        and window.available_end >= finish - tolerance
    )


def _daily_rows(connection) -> list[dict[str, object]]:
    query = f"""
        WITH days AS (
            SELECT DISTINCT local_day FROM samples
        ), aggregate_values AS (
            SELECT local_day,
                max(value) FILTER (WHERE source='classic.0' AND metric='daily_energy')
                    AS classic_counter_kwh,
                max(value) FILTER (WHERE source='classic.0' AND metric='battery_power')
                    AS classic_peak_w,
                min(value) FILTER (WHERE source='battery' AND metric='soc') AS soc_min,
                max(value) FILTER (WHERE source='battery' AND metric='soc') AS soc_max,
                arg_min(value, ts) FILTER (WHERE source='battery' AND metric='soc') AS soc_start,
                arg_max(value, ts) FILTER (WHERE source='battery' AND metric='soc') AS soc_end,
                avg(value) FILTER (WHERE source='load' AND metric='power') AS load_avg_w,
                max(value) FILTER (WHERE source='load' AND metric='power') AS load_peak_w,
                avg(value) FILTER (
                    WHERE source='tasmota.refrigeration' AND metric='power'
                ) AS refrigeration_avg_w,
                min(value) FILTER (WHERE source='ambient' AND metric='temperature')
                    AS ambient_min_c,
                max(value) FILTER (WHERE source='ambient' AND metric='temperature')
                    AS ambient_max_c
            FROM samples GROUP BY local_day
        ), intervals AS (
            SELECT local_day, source, metric, value,
                CASE
                    WHEN epoch(lead(ts) OVER (
                        PARTITION BY local_day, source, metric ORDER BY ts
                    ) - ts) BETWEEN 0 AND {MAX_INTEGRATION_GAP_SECONDS}
                    THEN epoch(lead(ts) OVER (
                        PARTITION BY local_day, source, metric ORDER BY ts
                    ) - ts)
                END AS seconds
            FROM samples
            WHERE (source, metric) IN (
                ('classic.0','battery_power'), ('load','power'),
                ('tasmota.refrigeration','power')
            )
        ), energy AS (
            SELECT local_day,
                sum(value * seconds) FILTER (
                    WHERE source='classic.0' AND metric='battery_power'
                ) / 3600000 AS classic_integrated_kwh,
                sum(value * seconds) FILTER (
                    WHERE source='load' AND metric='power'
                ) / 3600000 AS load_kwh,
                sum(value * seconds) FILTER (
                    WHERE source='tasmota.refrigeration' AND metric='power'
                ) / 3600000 AS refrigeration_kwh
            FROM intervals GROUP BY local_day
        )
        SELECT days.local_day, aggregate_values.* EXCLUDE(local_day),
               energy.* EXCLUDE(local_day)
        FROM days
        LEFT JOIN aggregate_values USING(local_day)
        LEFT JOIN energy USING(local_day)
        ORDER BY days.local_day
    """
    result = connection.execute(query)
    columns = [description[0] for description in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _scalar_extremes(connection, source: str, metric: str) -> tuple:
    return connection.execute(
        "SELECT min(value), max(value), avg(value), count(*) "
        "FROM samples WHERE source=? AND metric=?",
        [source, metric],
    ).fetchone()


def _count_zero(connection, source: str, metric: str) -> tuple[int, int]:
    count, zeroes = connection.execute(
        "SELECT count(*), count(*) FILTER (WHERE value=0) "
        "FROM samples WHERE source=? AND metric=?",
        [source, metric],
    ).fetchone()
    return int(count), int(zeroes)


def render_report(cache: Path, requested_start: datetime, requested_end: datetime,
                  zone: ZoneInfo) -> str:
    connection = duckdb.connect()
    cache_sql = sql_string(cache)
    zone_sql = sql_string(zone.key)
    requested_start_utc = requested_start.astimezone(timezone.utc).isoformat()
    requested_end_utc = requested_end.astimezone(timezone.utc).isoformat()
    connection.execute(
        "CREATE TEMP VIEW samples AS "
        "SELECT *, cast(captured_at AS TIMESTAMPTZ) AS ts, "
        f"cast(cast(captured_at AS TIMESTAMPTZ) AT TIME ZONE {zone_sql} AS DATE) "
        "AS local_day "
        f"FROM read_parquet({cache_sql}) "
        f"WHERE captured_at >= {sql_string(requested_start_utc)} "
        f"AND captured_at <= {sql_string(requested_end_utc)}"
    )
    coverage = connection.execute(
        "SELECT min(captured_at), max(captured_at), count(*), "
        "count(DISTINCT record_id) FROM samples"
    ).fetchone()
    if coverage[0] is None:
        raise SystemExit("The cache contains no samples in the requested window.")

    window = ReportWindow(
        requested_start=requested_start,
        requested_end=requested_end,
        available_start=datetime.fromisoformat(coverage[0]),
        available_end=datetime.fromisoformat(coverage[1]),
        timezone=zone,
    )
    daily = _daily_rows(connection)
    for row in daily:
        complete = _is_complete_day(row["local_day"], window)  # type: ignore[arg-type]
        row["complete"] = complete
        row["classic_kwh"] = (
            row["classic_counter_kwh"] if complete else row["classic_integrated_kwh"]
        )

    soc = _scalar_extremes(connection, "battery", "soc")
    voltage = _scalar_extremes(connection, "battery", "voltage")
    current = _scalar_extremes(connection, "battery", "current")
    power = _scalar_extremes(connection, "battery", "power")
    cell_delta = _scalar_extremes(connection, "battery", "cell_voltage_delta")
    min_cell_temp = _scalar_extremes(connection, "battery", "min_cell_temperature")
    max_cell_temp = _scalar_extremes(connection, "battery", "max_cell_temperature")
    max_cell_voltage = _scalar_extremes(connection, "battery", "max_cell_voltage")
    ambient = _scalar_extremes(connection, "ambient", "temperature")

    last_soc = connection.execute(
        "SELECT captured_at, value FROM samples WHERE source='battery' AND metric='soc' "
        "ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    first_soc = connection.execute(
        "SELECT value FROM samples WHERE source='battery' AND metric='soc' "
        "ORDER BY ts LIMIT 1"
    ).fetchone()

    battery_energy = connection.execute(f"""
        WITH intervals AS (
            SELECT value,
                CASE
                    WHEN epoch(lead(ts) OVER (ORDER BY ts) - ts)
                         BETWEEN 0 AND {MAX_INTEGRATION_GAP_SECONDS}
                    THEN epoch(lead(ts) OVER (ORDER BY ts) - ts)
                END AS interval_seconds
            FROM samples WHERE source='battery' AND metric='power'
        )
        SELECT
            sum(greatest(value,0) * interval_seconds) / 3600000,
            sum(greatest(-value,0) * interval_seconds) / 3600000
        FROM intervals
    """).fetchone()

    stages = connection.execute(
        "SELECT text, count(*), count(*) * 100.0 / sum(count(*)) OVER () "
        "FROM samples WHERE source='classic.0' AND metric='charge_stage' "
        "GROUP BY text ORDER BY count(*) DESC"
    ).fetchall()

    supervisor_count, supervisor_bad = _count_zero(connection, "supervisor", "ok")
    can_count, can_bad = _count_zero(connection, "battery.can", "ok")
    lan_count, lan_bad = _count_zero(connection, "network", "lan_reachable")
    inverter_count, inverter_off = _count_zero(connection, "magnum", "inverter_on")
    charger_count, charger_off = _count_zero(connection, "magnum", "charger_on")
    charge_enable_count, charge_disabled = _count_zero(connection, "battery", "charge_enable")
    discharge_enable_count, discharge_disabled = _count_zero(
        connection, "battery", "discharge_enable"
    )
    magnum_faults = connection.execute(
        "SELECT text, count(*) FROM samples WHERE source='magnum' AND metric='fault' "
        "GROUP BY text ORDER BY count(*) DESC"
    ).fetchall()
    errors = connection.execute(
        "SELECT metric, text, count(*) FROM samples "
        "WHERE source='supervisor' AND metric IN ('error','status_condition') "
        "GROUP BY metric,text ORDER BY count(*) DESC"
    ).fetchall()
    alarm_count = connection.execute(
        "SELECT count(*) FROM samples WHERE source='battery' "
        "AND metric IN ('alarm_flags','protection_flags') AND coalesce(text,'')<>''"
    ).fetchone()[0]
    epever = connection.execute(
        "SELECT min(value), max(value), count(*) FROM samples "
        "WHERE source='epever.1' AND metric='user_enabled'"
    ).fetchone()
    epever_power_count = connection.execute(
        "SELECT count(*) FROM samples WHERE source='epever.1' AND metric='battery_power'"
    ).fetchone()[0]

    ac_voltage = connection.execute(
        "SELECT min(value), quantile_cont(value,0.01), quantile_cont(value,0.5), "
        "quantile_cont(value,0.99), max(value) FROM samples "
        "WHERE source='magnum' AND metric='ac_voltage_out'"
    ).fetchone()
    ac_frequency = _scalar_extremes(connection, "magnum", "ac_frequency")
    transformer_temp = _scalar_extremes(connection, "magnum", "transformer_temperature")
    fet_temp = _scalar_extremes(connection, "magnum", "fet_temperature")

    total_classic = sum(float(row["classic_kwh"] or 0) for row in daily)
    total_load = sum(float(row["load_kwh"] or 0) for row in daily)
    total_refrigeration = sum(float(row["refrigeration_kwh"] or 0) for row in daily)
    duration_hours = max(
        (window.available_end - window.available_start).total_seconds() / 3600, 0.0
    )

    lines = [
        "# Cloud telemetry performance report",
        "",
        f"**Requested:** {_fmt_local(requested_start, zone)} through "
        f"{_fmt_local(requested_end, zone)} (inclusive)  ",
        f"**Available:** {_fmt_local(window.available_start, zone)} through "
        f"{_fmt_local(window.available_end, zone)}  ",
        f"**Cache:** `{cache}`",
        "",
        "## Summary",
        "",
        f"- Array 0 delivered **{total_classic:.2f} kWh**; estimated system demand "
        f"was **{total_load:.2f} kWh**.",
        f"- Battery SOC started at **{_fmt(first_soc[0] if first_soc else None, 0)}%**, "
        f"ranged from **{_fmt(soc[0], 0)}% to {_fmt(soc[1], 0)}%**, and the last "
        f"available SOC was **{_fmt(last_soc[1] if last_soc else None, 0)}%** at "
        f"**{_fmt_local(datetime.fromisoformat(last_soc[0]), zone) if last_soc else '—'}**.",
        f"- Refrigeration used approximately **{total_refrigeration:.2f} kWh**.",
    ]
    if epever[2] and epever[0] == 0 and epever[1] == 0:
        lines.append("- Array 1 was user-disabled throughout the available window.")
    elif epever_power_count == 0:
        lines.append("- Array 1 supplied no battery-power telemetry in this window.")

    lines.extend([
        "",
        "## Daily performance",
        "",
        "| Local day | Coverage | Array 0 | Est. demand | SOC start → low → high → end | "
        "Solar peak | Refrigeration |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for row in daily:
        coverage_label = "complete" if row["complete"] else "partial"
        soc_path = " → ".join(_fmt(row[key], 0) for key in (
            "soc_start", "soc_min", "soc_max", "soc_end"
        ))
        lines.append(
            f"| {row['local_day']} | {coverage_label} | {_fmt(row['classic_kwh'], 2)} kWh | "
            f"{_fmt(row['load_kwh'], 2)} kWh | {soc_path}% | "
            f"{_fmt((row['classic_peak_w'] or 0) / 1000, 2)} kW | "
            f"{_fmt(row['refrigeration_kwh'], 2)} kWh |"
        )

    lines.extend([
        "",
        "Full local days use the Classic daily-energy counter. Partial days use "
        "time-integrated Classic battery power. Demand, refrigeration, and battery "
        "throughput are time-integrated with gaps over three minutes excluded.",
        "",
        "## Battery",
        "",
        f"- Voltage: **{_fmt(voltage[0], 2)}–{_fmt(voltage[1], 2)} V**.",
        f"- Current: **{_fmt(current[0], 1)} to {_fmt(current[1], 1)} A**.",
        f"- Power: **{_fmt(power[0] / 1000 if power[0] is not None else None, 2)} to "
        f"{_fmt(power[1] / 1000 if power[1] is not None else None, 2)} kW**.",
        f"- Charge/discharge throughput: **{_fmt(battery_energy[0], 2)} / "
        f"{_fmt(battery_energy[1], 2)} kWh**.",
        f"- Cell spread: average **{_fmt(cell_delta[2], 1)} mV**, maximum "
        f"**{_fmt(cell_delta[1], 1)} mV**.",
        f"- Maximum cell voltage: **{_fmt(max_cell_voltage[1], 3)} V**.",
        f"- Cell-temperature envelope: **{_fmt(min_cell_temp[0], 1)}–"
        f"{_fmt(max_cell_temp[1], 1)} °C**; ambient **{_fmt(ambient[0], 1)}–"
        f"{_fmt(ambient[1], 1)} °C**.",
        f"- Non-empty BMS alarm/protection samples: **{alarm_count}**.",
        f"- Charge disabled samples: **{charge_disabled}/{charge_enable_count}**; "
        f"discharge disabled: **{discharge_disabled}/{discharge_enable_count}**.",
        "",
        "## Charging stages",
        "",
    ])
    if stages:
        for stage, count, percent in stages:
            hours = duration_hours * percent / 100
            lines.append(f"- {stage or 'Unknown'}: **{percent:.1f}%** (~{hours:.1f} h; {count} samples).")
    else:
        lines.append("- No Classic charge-stage samples.")

    lines.extend([
        "",
        "## Inverter and reliability",
        "",
        f"- Inverter off samples: **{inverter_off}/{inverter_count}**; Magnum charger-on "
        f"samples: **{charger_count - charger_off}/{charger_count}**.",
        f"- AC output voltage min/1st percentile/median/99th percentile/max: "
        f"**{_fmt(ac_voltage[0], 0)} / {_fmt(ac_voltage[1], 0)} / "
        f"{_fmt(ac_voltage[2], 0)} / {_fmt(ac_voltage[3], 0)} / "
        f"{_fmt(ac_voltage[4], 0)} V**.",
        f"- AC frequency: **{_fmt(ac_frequency[0], 1)}–{_fmt(ac_frequency[1], 1)} Hz**.",
        f"- Magnum transformer/FET maximum temperatures: **{_fmt(transformer_temp[1], 1)} / "
        f"{_fmt(fet_temp[1], 1)} °C**.",
        f"- Supervisor unhealthy samples: **{supervisor_bad}/{supervisor_count}**; "
        f"battery-CAN unhealthy: **{can_bad}/{can_count}**; LAN unreachable: "
        f"**{lan_bad}/{lan_count}**.",
    ])
    non_none_faults = [(name, count) for name, count in magnum_faults if name not in (None, "NONE")]
    lines.append(
        f"- Magnum fault samples: **{sum(count for _, count in non_none_faults)}**."
    )
    if errors:
        lines.extend(["", "Recorded supervisor conditions:"])
        for metric, text, count in errors:
            lines.append(f"- {metric}: {text} ({count} sample{'s' if count != 1 else ''})")
    else:
        lines.append("- No supervisor error or status-condition text was recorded.")

    if window.available_end + timedelta(minutes=2) < requested_end:
        lines.extend([
            "",
            "## Data freshness",
            "",
            f"The requested end is later than the newest cached sample. Results stop at "
            f"**{_fmt_local(window.available_end, zone)}**.",
        ])
    connection.close()
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--start", required=True, help="Inclusive ISO local start")
    parser.add_argument("--end", required=True, help="Inclusive ISO local end")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE,
                        help=f"Site timezone (default: {DEFAULT_TIMEZONE})")
    parser.add_argument("--credentials-backup", type=Path,
                        help="Migration .tar.gz containing etc/offgrid-power.env")
    parser.add_argument("--cache", type=Path,
                        help="Parquet cache path; reused when it already exists")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path("/private/tmp/offgrid-cloud-report-cache"),
                        help="Default cache directory when --cache is omitted")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-download and atomically replace an existing cache")
    parser.add_argument("--output", type=Path,
                        help="Write Markdown here instead of stdout")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        zone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"Unknown timezone: {args.timezone}") from exc
    start = parse_local_datetime(args.start, zone)
    end = parse_local_datetime(args.end, zone)
    if end < start:
        raise SystemExit("--end must not be earlier than --start")

    cache = args.cache or cache_path_for(start, end, args.cache_dir)
    if args.refresh or not cache.exists():
        config = load_cloud_config(args.credentials_backup)
        materialize_cloud_samples(config, start, end, cache)

    report = render_report(cache, start, end, zone)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
        print(args.output)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
