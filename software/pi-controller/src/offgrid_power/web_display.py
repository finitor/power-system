"""Primitive HTML rendering and serving for supervisor metrics."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock
from typing import Callable
from urllib.parse import urlparse

from .supervisor import Supervisor, SupervisorSnapshot
from .terminal_display import format_cell_location_for_display, format_time
from .weather import WeatherReport, weather_code_text


KINDLE_REFRESH_SECONDS = 60
WEATHER_STALE_AFTER = timedelta(hours=1)
MIDNIGHT_SOC_UNAVAILABLE = "00:00:00h SOC unavailable"
BATTERY_IDLE_CURRENT_A = 0.5
ROLLING_LOAD_WINDOW = timedelta(hours=3)


@dataclass(frozen=True)
class DisplayResponse:
    status: HTTPStatus
    content_type: str
    body: bytes


@dataclass(frozen=True)
class LoadSummary:
    current_a: float
    power_w: int
    average_today_text: str | None = None
    today_text: str | None = None
    remaining_text: str | None = None
    rolling_average_a: float | None = None
    rolling_average_w: float | None = None


@dataclass(frozen=True)
class LoadSample:
    captured_at: datetime
    current_a: float
    power_w: int
    soc_percent: int | None = None
    voltage_v: float | None = None


class SnapshotCache:
    def __init__(self) -> None:
        self._snapshot: SupervisorSnapshot | None = None
        self._load_summary: LoadSummary | None = None
        self._lock = Lock()

    def set(self, snapshot: SupervisorSnapshot, load_summary: LoadSummary | None = None) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._load_summary = load_summary

    def get(self) -> SupervisorSnapshot:
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("no supervisor snapshot has been captured yet")
            return self._snapshot

    def get_load_summary(self) -> LoadSummary | None:
        with self._lock:
            return self._load_summary


def is_kindle_user_agent(user_agent: str) -> bool:
    normalized = user_agent.lower()
    return "kindle" in normalized or "silk/" in normalized


def format_kindle_time(captured_at: datetime) -> str:
    return captured_at.astimezone().strftime("%H:%M:%S %Z")


def render_kindle_snapshot(
    snapshot: SupervisorSnapshot,
    refresh_seconds: int = KINDLE_REFRESH_SECONDS,
    load_summary: LoadSummary | None = None,
) -> str:
    status = snapshot.status_text
    updated = format_kindle_time(snapshot.captured_at)
    soc_text = _soc_text(snapshot)
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        f'<meta http-equiv="refresh" content="{refresh_seconds}">',
        "<title>Off-Grid Power</title>",
        "<style>",
        "body{font-family:serif;color:#000;background:#fff;margin:4px;font-size:17px;-webkit-text-size-adjust:100%;text-size-adjust:100%;}",
        "h2{font-size:19px;margin:8px 0 2px 0;border-bottom:1px solid #000;}",
        "ul{margin:0 0 4px 18px;padding:0;}",
        "li{line-height:1.15;}",
        "table{border-collapse:collapse;width:100%;}",
        "td{font-size:17px;line-height:1.18;padding:1px 0;vertical-align:top;border-bottom:1px solid #ccc;}",
        "td:first-child{font-size:17px;font-weight:bold;width:32%;}",
        ".bad{font-weight:bold;}",
        ".summary-table{margin:0 0 6px 0;border-bottom:1px solid #000;}",
        ".summary-table td{font-size:19px;font-weight:bold;border-bottom:0;padding:0 0 2px 0;}",
        ".summary-table .soc-cell{font-size:36px;line-height:1;text-align:left;vertical-align:middle;width:32%;}",
        ".summary-table .meta-cell{font-size:17px;line-height:1.05;text-align:left;vertical-align:middle;width:52%;}",
        ".summary-table .button-cell{font-size:17px;line-height:1;text-align:right;vertical-align:middle;width:16%;}",
        ".top-link{font-size:17px;line-height:2.1;color:#000;text-decoration:none;border:1px solid #000;padding:0 10px;display:block;text-align:center;}",
        ".small{font-size:13px;}",
        "</style>",
        "</head>",
        "<body>",
        '<table class="summary-table">',
        f'<tr><td class="soc-cell">SOC {escape(soc_text)}</td><td class="meta-cell">Updated: {escape(updated)}<br>Status: {escape(status)}</td><td class="button-cell"><a class="top-link" href="/weather">Weather</a></td></tr>',
        "</table>",
    ]
    lines.extend(_load_section(load_summary))
    lines.extend(_battery_section(snapshot))
    lines.extend(_charge_controller_sections(snapshot))
    lines.extend(_inverter_charger_section(snapshot))
    lines.extend(_temperature_section(snapshot))

    if snapshot.errors:
        lines.append("<h2>Errors</h2>")
        lines.append("<ul>")
        for error in snapshot.errors:
            lines.append(f"<li>{escape(error)}</li>")
        lines.append("</ul>")
    if snapshot.status_conditions:
        lines.append("<h2>Status Conditions</h2>")
        lines.append("<ul>")
        for condition in snapshot.status_conditions:
            lines.append(f"<li>{escape(condition)}</li>")
        lines.append("</ul>")

    lines.extend(
        [
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines)


def render_kindle_weather(
    report: WeatherReport | None,
    refresh_seconds: int = KINDLE_REFRESH_SECONDS,
    now: datetime | None = None,
) -> str:
    reference = now or datetime.now().astimezone()
    status_text = "Weather unavailable"
    updated = "never"
    if report is not None and report.data:
        updated = format_kindle_time(report.fetched_at)
        status_text = "stale forecast" if report.stale else "forecast"
    too_stale = (
        report is not None
        and report.data
        and report.stale
        and reference.astimezone() - report.fetched_at.astimezone() >= WEATHER_STALE_AFTER
    )
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        f'<meta http-equiv="refresh" content="{refresh_seconds}">',
        "<title>Off-Grid Weather</title>",
        "<style>",
        "body{font-family:serif;color:#000;background:#fff;margin:4px;font-size:17px;-webkit-text-size-adjust:100%;text-size-adjust:100%;}",
        "h2{font-size:19px;margin:8px 0 2px 0;border-bottom:1px solid #000;}",
        "table{border-collapse:collapse;width:100%;}",
        "td{font-size:17px;line-height:1.18;padding:1px 0;vertical-align:top;border-bottom:1px solid #ccc;}",
        "td:first-child{font-size:17px;font-weight:bold;width:38%;}",
        ".summary-table{margin:0 0 6px 0;border-bottom:1px solid #000;}",
        ".summary-table td{font-size:19px;font-weight:bold;border-bottom:0;padding:0 0 2px 0;}",
        ".summary-table .weather-cell{font-size:30px;line-height:1;text-align:left;vertical-align:middle;width:38%;}",
        ".summary-table .meta-cell{font-size:17px;line-height:1.05;text-align:left;vertical-align:middle;width:46%;}",
        ".summary-table .button-cell{font-size:17px;line-height:1;text-align:right;vertical-align:middle;width:16%;}",
        ".top-link{font-size:17px;line-height:2.1;color:#000;text-decoration:none;border:1px solid #000;padding:0 10px;display:block;text-align:center;}",
        ".small{font-size:13px;}",
        "</style>",
        "</head>",
        "<body>",
    ]
    if too_stale:
        lines.extend(
            [
                '<table class="summary-table">',
                f'<tr><td class="weather-cell">Weather</td><td class="meta-cell">Weather service has been unreachable since {escape(format_time(report.fetched_at))}</td><td class="button-cell"><a class="top-link" href="/kindle">Power</a></td></tr>',
                "</table>",
                "<h2>Conditions</h2>",
                "<p>Weather service unreachable.</p>",
            ]
        )
        if report.error:
            lines.append(f'<p class="small">{escape(report.error)}</p>')
    elif report is None or not report.data:
        lines.extend(
            [
                '<table class="summary-table">',
                f'<tr><td class="weather-cell">Weather</td><td class="meta-cell">Updated: {escape(updated)}<br>{escape(status_text)}</td><td class="button-cell"><a class="top-link" href="/kindle">Power</a></td></tr>',
                "</table>",
                "<h2>Conditions</h2>",
                "<p>Weather unavailable.</p>",
            ]
        )
        if report is not None and report.error:
            lines.append(f'<p class="small">{escape(report.error)}</p>')
    else:
        current = report.data.get("current") or {}
        temp = _format_number(current.get("temperature_2m"), "C", decimals=1)
        condition = weather_code_text(current.get("weather_code"))
        lines.extend(
            [
                '<table class="summary-table">',
                f'<tr><td class="weather-cell">{escape(temp or "--")}</td><td class="meta-cell">{escape(report.label)}: {escape(condition)}<br>Updated: {escape(updated)}</td><td class="button-cell"><a class="top-link" href="/kindle">Power</a></td></tr>',
                "</table>",
                "<h2>Current</h2>",
                "<table>",
                _weather_row("Feels Like", _format_number(current.get("apparent_temperature"), "C", decimals=1)),
                _weather_row("Humidity", _format_number(current.get("relative_humidity_2m"), "%", decimals=0)),
                _weather_row("Cloud", _format_number(current.get("cloud_cover"), "%", decimals=0)),
                _weather_row(
                    "Wind",
                    _wind_text(current.get("wind_speed_10m"), current.get("wind_gusts_10m"), current.get("wind_direction_10m")),
                ),
                _weather_row("Precip Now", _precip_text(current.get("precipitation"), current.get("rain"), current.get("snowfall"))),
                "</table>",
            ]
        )
        lines.extend(_hourly_weather_section(report.data))
        lines.extend(_daily_weather_section(report.data))
        lines.extend(_solar_irradiance_section(current))
        lines.extend(_astronomy_weather_section(report.data))
        if report.stale:
            lines.append("<p class=\"small\">Using last cached weather. WAN fetch failed.</p>")
        if report.error:
            lines.append(f'<p class="small">{escape(report.error)}</p>')

    lines.extend(
        [
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines)


def route_display_request(
    snapshot: SupervisorSnapshot,
    path: str,
    user_agent: str,
    load_summary: LoadSummary | None = None,
    weather_report: WeatherReport | None = None,
) -> DisplayResponse:
    parsed_path = urlparse(path).path
    if parsed_path in {"/api/v1/health", "/api/v1/snapshot"}:
        return route_api_request(snapshot, parsed_path, load_summary=load_summary)
    if parsed_path not in {"/", "/kindle", "/display", "/weather", "/healthz"}:
        return DisplayResponse(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
    if parsed_path == "/healthz":
        status = HTTPStatus.OK if snapshot.ok else HTTPStatus.SERVICE_UNAVAILABLE
        body = b"ok\n" if snapshot.ok else b"error\n"
        return DisplayResponse(status, "text/plain; charset=utf-8", body)
    if parsed_path == "/weather":
        html = render_kindle_weather(weather_report)
        return DisplayResponse(HTTPStatus.OK, "text/html; charset=utf-8", html.encode("utf-8"))

    html = render_kindle_snapshot(snapshot, load_summary=load_summary)
    content_type = "text/html; charset=utf-8"
    if parsed_path == "/kindle" or is_kindle_user_agent(user_agent):
        return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))
    return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))


def route_api_request(
    snapshot: SupervisorSnapshot,
    path: str,
    load_summary: LoadSummary | None = None,
    now: datetime | None = None,
) -> DisplayResponse:
    if path == "/api/v1/health":
        payload = health_api_payload(snapshot, now=now)
        status = HTTPStatus.OK if snapshot.ok else HTTPStatus.SERVICE_UNAVAILABLE
        return _json_response(status, payload)
    if path == "/api/v1/snapshot":
        return _json_response(HTTPStatus.OK, snapshot_api_payload(snapshot, load_summary=load_summary, now=now))
    return _json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})


def _hourly_weather_section(data: dict) -> list[str]:
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return []
    rows = ["<h2>Next Hours</h2>", "<table>"]
    for index, hour in enumerate(times[:8]):
        rows.append(
            _weather_row(
                _short_time(hour),
                "  ".join(
                    item
                    for item in [
                        weather_code_text(_indexed(hourly.get("weather_code"), index)),
                        _format_number(_indexed(hourly.get("temperature_2m"), index), "C", decimals=1),
                        _format_number(_indexed(hourly.get("precipitation_probability"), index), "% precip", decimals=0),
                        _format_number(_indexed(hourly.get("wind_speed_10m"), index), "km/h", decimals=0),
                    ]
                    if item
                ),
            )
        )
    rows.append("</table>")
    return rows


def _daily_weather_section(data: dict) -> list[str]:
    daily = data.get("daily") or {}
    days = daily.get("time") or []
    if not days:
        return []
    rows = ["<h2>Forecast</h2>", "<table>"]
    for index, day in enumerate(days[:3]):
        rows.append(
            _weather_row(
                _short_day(day),
                "  ".join(
                    item
                    for item in [
                        weather_code_text(_indexed(daily.get("weather_code"), index)),
                        _daily_temperature_text(
                            _indexed(daily.get("temperature_2m_min"), index),
                            _indexed(daily.get("temperature_2m_max"), index),
                        ),
                        _format_number(_indexed(daily.get("precipitation_probability_max"), index), "% precip", decimals=0),
                        _format_number(_indexed(daily.get("precipitation_sum"), index), "mm", decimals=1),
                    ]
                    if item
                ),
            )
        )
    rows.append("</table>")
    return rows


def _solar_irradiance_section(current: dict) -> list[str]:
    return [
        "<h2>Solar Irradiance</h2>",
        "<table>",
        _weather_row(
            "Global Horizontal (GHI)",
            _format_number(current.get("shortwave_radiation"), "W/m2", decimals=0),
        ),
        _weather_row("Direct Radiation", _format_number(current.get("direct_radiation"), "W/m2", decimals=0)),
        _weather_row("Diffuse Radiation", _format_number(current.get("diffuse_radiation"), "W/m2", decimals=0)),
        _weather_row(
            "Direct Normal (DNI)",
            _format_number(current.get("direct_normal_irradiance"), "W/m2", decimals=0),
        ),
        "</table>",
    ]


def _astronomy_weather_section(data: dict) -> list[str]:
    daily = data.get("daily") or {}
    aurora = data.get("aurora") or {}
    rows = ["<h2>Astronomy</h2>", "<table>"]
    rows.append(
        _weather_row(
            "Sun",
            _sun_text(
                _indexed(daily.get("sunrise"), 0),
                _indexed(daily.get("sunset"), 0),
            ),
        )
    )
    rows.append(_weather_row("Moon", _moon_phase_text(_indexed(daily.get("moon_phase"), 0))))
    rows.append(_weather_row_html("Aurora", _aurora_html(aurora)))
    rows.append("</table>")
    return rows


def _weather_row(label: str, value: str | None) -> str:
    return f"<tr><td>{escape(label)}</td><td>{escape(value or '--')}</td></tr>"


def _weather_row_html(label: str, value: str | None) -> str:
    return f"<tr><td>{escape(label)}</td><td>{value or '--'}</td></tr>"


def _format_number(value: object, suffix: str, decimals: int = 1) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return f"{number:.{decimals}f}{suffix}"


def _wind_text(speed: object, gust: object, direction: object) -> str | None:
    speed_text = _format_number(speed, "km/h", decimals=0)
    if speed_text is None:
        return None
    gust_text = _format_number(gust, "km/h gust", decimals=0)
    direction_text = _wind_direction_text(direction)
    parts = [speed_text]
    if gust_text:
        parts.append(gust_text)
    if direction_text:
        parts.append(direction_text)
    return "  ".join(parts)


def _wind_direction_text(value: object) -> str | None:
    try:
        degrees = float(value)
    except (TypeError, ValueError):
        return None
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[int((degrees + 22.5) // 45) % 8]


def _precip_text(precipitation: object, rain: object, snowfall: object) -> str | None:
    parts = []
    precip_text = _format_number(precipitation, "mm", decimals=1)
    rain_text = _format_number(rain, "mm rain", decimals=1)
    snow_text = _format_number(snowfall, "cm snow", decimals=1)
    if precip_text:
        parts.append(precip_text)
    if rain_text:
        parts.append(rain_text)
    if snow_text:
        parts.append(snow_text)
    return "  ".join(parts) if parts else None


def _moon_phase_text(value: object) -> str | None:
    try:
        phase = float(value)
    except (TypeError, ValueError):
        return None
    if phase < 0.03 or phase > 0.97:
        name = "new"
    elif phase < 0.22:
        name = "waxing crescent"
    elif phase < 0.28:
        name = "first quarter"
    elif phase < 0.47:
        name = "waxing gibbous"
    elif phase < 0.53:
        name = "full"
    elif phase < 0.72:
        name = "waning gibbous"
    elif phase < 0.78:
        name = "last quarter"
    else:
        name = "waning crescent"
    return f"{name} ({phase:.2f})"


def _sun_text(sunrise: object, sunset: object) -> str | None:
    sunrise_text = _short_time(sunrise)
    sunset_text = _short_time(sunset)
    if sunrise_text == "--" and sunset_text == "--":
        return None
    return f"rise {sunrise_text}  set {sunset_text}"


def _aurora_html(aurora: object) -> str | None:
    if not isinstance(aurora, dict):
        return None
    tonight = aurora.get("tonight")
    if aurora.get("error"):
        return "unavailable"
    probability = _format_number(aurora.get("probability_percent"), "%", decimals=0)
    if probability is None:
        return None
    forecast_time = aurora.get("forecast_time")
    now_line = f"now {escape(probability)}"
    if isinstance(forecast_time, str):
        now_line = f"{now_line} valid {escape(_short_time(forecast_time))}"
    return f"{now_line}<br>{_aurora_tonight_text(tonight)}"


def _aurora_tonight_text(tonight: object) -> str:
    if not isinstance(tonight, dict) or tonight.get("error"):
        return "tonight unavailable"
    kp = _format_number(tonight.get("peak_kp"), "", decimals=1)
    likelihood = tonight.get("likelihood")
    if kp is None or not isinstance(likelihood, str):
        return "tonight unavailable"
    peak_time = tonight.get("peak_time")
    time_text = _short_time(peak_time) if isinstance(peak_time, str) else "--"
    scale = tonight.get("noaa_scale")
    scale_text = f" {escape(str(scale))}" if scale else ""
    return f"tonight {escape(likelihood)} peak Kp {escape(kp)}{scale_text} at {escape(time_text)}"


def _daily_temperature_text(low: object, high: object) -> str | None:
    low_text = _format_number(low, "C", decimals=1)
    high_text = _format_number(high, "C", decimals=1)
    if low_text and high_text:
        return f"{low_text}-{high_text}"
    return high_text or low_text


def _indexed(values: object, index: int) -> object:
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _short_time(value: object) -> str:
    if not isinstance(value, str):
        return "--"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%H:%M")


def _short_day(value: object) -> str:
    if not isinstance(value, str):
        return "--"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%a %m/%d")


def health_api_payload(snapshot: SupervisorSnapshot, now: datetime | None = None) -> dict:
    return {
        "schema_version": 1,
        "ok": snapshot.ok,
        "status": snapshot.status_text,
        "captured_at": snapshot.captured_at.isoformat(),
        "age_seconds": _age_seconds(snapshot.captured_at, now=now),
        "errors": list(snapshot.errors),
    }


def snapshot_api_payload(
    snapshot: SupervisorSnapshot,
    load_summary: LoadSummary | None = None,
    now: datetime | None = None,
    site_id: str = "cabin",
) -> dict:
    return {
        "schema_version": 1,
        "site_id": site_id,
        "captured_at": snapshot.captured_at.isoformat(),
        "age_seconds": _age_seconds(snapshot.captured_at, now=now),
        "status": {
            "ok": snapshot.ok,
            "severity": snapshot.status_text,
            "errors": list(snapshot.errors),
            "conditions": list(snapshot.status_conditions),
        },
        "battery": _battery_api_payload(snapshot),
        "solar": _solar_api_payload(snapshot),
        "inverter": _inverter_api_payload(snapshot),
        "load": _load_api_payload(load_summary),
        "ambient": _ambient_api_payload(snapshot),
    }


def _json_response(status: HTTPStatus, payload: dict) -> DisplayResponse:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    return DisplayResponse(status, "application/json; charset=utf-8", body)


def _age_seconds(captured_at: datetime, now: datetime | None = None) -> int:
    now = now or datetime.now(captured_at.tzinfo)
    return max(0, int((now - captured_at).total_seconds()))


def _battery_api_payload(snapshot: SupervisorSnapshot) -> dict | None:
    battery = snapshot.battery
    if battery is None:
        return None

    state = battery.state_of_charge
    measurements = battery.measurements
    extended = battery.extended_measurements
    limits = battery.charge_limits
    flags = battery.request_flags
    status = battery.status
    payload: dict = {
        "soc_percent": state.soc_percent if state is not None else None,
        "soh_percent": state.soh_percent if state is not None else None,
        "voltage_v": measurements.voltage_v if measurements is not None else None,
        "current_a": measurements.current_a if measurements is not None else None,
        "power_w": (measurements.voltage_v * measurements.current_a) if measurements is not None else None,
        "temperature_c": measurements.temperature_c if measurements is not None else None,
        "cell_min_v": extended.min_cell_voltage_v if extended is not None else None,
        "cell_max_v": extended.max_cell_voltage_v if extended is not None else None,
        "cell_delta_mv": None,
        "cell_min_pack_number": extended.min_cell_pack_number if extended is not None else None,
        "cell_min_number": extended.min_cell_number if extended is not None else None,
        "cell_min_location": extended.min_cell_location_text() if extended is not None else None,
        "cell_max_pack_number": extended.max_cell_pack_number if extended is not None else None,
        "cell_max_number": extended.max_cell_number if extended is not None else None,
        "cell_max_location": extended.max_cell_location_text() if extended is not None else None,
        "cell_temperature_min_c": extended.min_cell_temperature_c if extended is not None else None,
        "cell_temperature_max_c": extended.max_cell_temperature_c if extended is not None else None,
        "installed_capacity_ah": extended.installed_capacity_ah if extended is not None else None,
        "charge_enabled": flags.charge_enable if flags is not None else None,
        "discharge_enabled": flags.discharge_enable if flags is not None else None,
        "force_charge_1": flags.force_charge_1 if flags is not None else None,
        "force_charge_2": flags.force_charge_2 if flags is not None else None,
        "full_charge_request": flags.full_charge_request if flags is not None else None,
        "charge_voltage_limit_v": limits.charge_voltage_limit_v if limits is not None else None,
        "charge_current_limit_a": limits.charge_current_limit_a if limits is not None else None,
        "discharge_current_limit_a": limits.discharge_current_limit_a if limits is not None else None,
        "discharge_voltage_limit_v": limits.discharge_voltage_limit_v if limits is not None else None,
        "module_count": status.module_count if status is not None else None,
        "protection_flags": list(status.protection_flags) if status is not None else [],
        "alarm_flags": list(status.alarm_flags) if status is not None else [],
        "manufacturer": battery.manufacturer,
        "manufacturer_marker": status.manufacturer_marker if status is not None else None,
    }
    if extended is not None and extended.min_cell_voltage_v is not None and extended.max_cell_voltage_v is not None:
        payload["cell_delta_mv"] = round((extended.max_cell_voltage_v - extended.min_cell_voltage_v) * 1000)
    return payload


def _solar_api_payload(snapshot: SupervisorSnapshot) -> list[dict]:
    if snapshot.classic is None:
        return []
    classic = snapshot.classic
    settings = snapshot.classic_settings
    return [
        {
            "id": "classic.0",
            "label": "Classic 200",
            "captured_at": classic.captured_at.isoformat(),
            "battery_voltage_v": classic.battery_voltage_v,
            "battery_current_a": classic.battery_current_a,
            "battery_power_w": classic.battery_power_w,
            "pv_voltage_v": classic.pv_voltage_v,
            "pv_current_a": classic.pv_current_a,
            "daily_energy_kwh": classic.daily_energy_kwh,
            "daily_amp_hours_ah": classic.daily_amp_hours_ah,
            "lifetime_energy_kwh": classic.lifetime_energy_kwh,
            "lifetime_amp_hours_ah": classic.lifetime_amp_hours_ah,
            "last_voc_v": classic.last_voc_v,
            "highest_input_voltage_v": classic.highest_input_voltage_v,
            "charge_stage_code": classic.charge_stage_code,
            "charge_stage": classic.charge_stage,
            "state_code": classic.state_code,
            "state": classic.state,
            "info_flags": classic.info_flags,
            "active_flags": list(classic.active_flags),
            "temperatures_c": {
                "battery": classic.battery_temp_c,
                "fet": classic.fet_temp_c,
                "pcb": classic.pcb_temp_c,
            },
            "settings": None
            if settings is None
            else {
                "current_limit_a": settings.battery_current_limit_a,
                "absorb_voltage_v": settings.absorb_voltage_v,
                "float_voltage_v": settings.float_voltage_v,
                "equalize_voltage_v": settings.equalize_voltage_v,
                "absorb_time_s": settings.absorb_time_s,
            },
        }
    ]


def _inverter_api_payload(snapshot: SupervisorSnapshot) -> dict | None:
    inv = snapshot.magnum
    if inv is None:
        return None
    return {
        "captured_at": inv.captured_at.isoformat(),
        "dc_volts": inv.dc_volts,
        "dc_amps": inv.dc_amps,
        "dc_power_w": inv.dc_power_w,
        "ac_volts_out": inv.ac_volts_out,
        "ac_amps_out": inv.ac_amps_out,
        "ac_freq_hz": inv.ac_freq_hz,
        "ac_volts_in": inv.ac_volts_in,
        "ac_amps_in": inv.ac_amps_in,
        "inverter_on": inv.inverter_on,
        "charger_on": inv.charger_on,
        "status": inv.status_name,
        "status_label": inv.status_label(),
        "fault": inv.fault_name,
        "battery_temp_c": inv.battery_temp_c,
        "transformer_temp_c": inv.transformer_temp_c,
        "fet_temp_c": inv.fet_temp_c,
        "settings": {
            "absorb_v": inv.absorb_v,
            "float_v": inv.float_v,
            "absorb_time_hr": inv.absorb_time_hr,
            "shore_amps": inv.shore_amps,
            "charger_amps_pct": inv.charger_amps_pct,
        },
    }


def _load_api_payload(load_summary: LoadSummary | None) -> dict | None:
    if load_summary is None:
        return None
    return {
        "current_a": load_summary.current_a,
        "power_w": load_summary.power_w,
        "average_today_text": load_summary.average_today_text,
        "today_text": load_summary.today_text,
        "remaining_text": load_summary.remaining_text,
        "rolling_average_a": load_summary.rolling_average_a,
        "rolling_average_w": load_summary.rolling_average_w,
        "estimated_autonomy_hours": _hours_text_value(load_summary.remaining_text),
    }


def _hours_text_value(text: str | None) -> float | None:
    if text is None or not text.endswith("h"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None


def _ambient_api_payload(snapshot: SupervisorSnapshot) -> dict | None:
    if snapshot.ambient is None:
        return None
    return {
        "captured_at": snapshot.ambient.captured_at.isoformat(),
        "temperature_c": snapshot.ambient.temperature_c,
        "humidity_percent": snapshot.ambient.humidity_percent,
    }


def run_display_server(
    supervisor: Supervisor,
    host: str = "0.0.0.0",
    port: int = 8080,
    snapshot_provider: Callable[[], SupervisorSnapshot] | None = None,
    load_summary_provider: Callable[[], LoadSummary | None] | None = None,
    weather_provider: Callable[[], WeatherReport | None] | None = None,
    access_log_path: str | None = None,
) -> None:
    provider = snapshot_provider or supervisor.read_snapshot
    logger = AccessLogger(access_log_path)
    load_tracker = LoadTracker(sample_buffer=LoadSampleBuffer())

    class Handler(BaseHTTPRequestHandler):
        server_version = "OffGridPowerDisplay/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            try:
                snapshot = provider()
            except Exception as exc:  # noqa: BLE001 - HTTP display should show readiness errors.
                if urlparse(self.path).path.startswith("/api/"):
                    body = json.dumps(
                        {
                            "schema_version": 1,
                            "ok": False,
                            "status": "UNAVAILABLE",
                            "error": str(exc),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8") + b"\n"
                    self.send_response(HTTPStatus.SERVICE_UNAVAILABLE.value)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    logger.log(
                        self.client_address[0],
                        self.path,
                        self.headers.get("User-Agent", ""),
                        HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                body = render_snapshot_unavailable(exc).encode("utf-8")
                self.send_response(HTTPStatus.SERVICE_UNAVAILABLE.value)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                logger.log(
                    self.client_address[0],
                    self.path,
                    self.headers.get("User-Agent", ""),
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            if urlparse(self.path).path == "/healthz":
                load_summary = None
                weather_report = None
            elif load_summary_provider is not None:
                load_summary = load_summary_provider()
                weather_report = weather_provider() if weather_provider is not None and urlparse(self.path).path == "/weather" else None
            else:
                load_summary = load_tracker.update(snapshot)
                weather_report = weather_provider() if weather_provider is not None and urlparse(self.path).path == "/weather" else None
            response = route_display_request(
                snapshot,
                self.path,
                self.headers.get("User-Agent", ""),
                load_summary=load_summary,
                weather_report=weather_report,
            )
            status = response.status
            self.send_response(response.status.value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)
            logger.log(self.client_address[0], self.path, self.headers.get("User-Agent", ""), status)

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
            return

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever()


class AccessLogger:
    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None

    def log(self, client: str, path: str, user_agent: str, status: HTTPStatus) -> None:
        line = (
            f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
            f"{client} {status.value} {path!r} {user_agent!r}\n"
        )
        if self.path is None:
            print(line, end="", flush=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)


class LoadTracker:
    def __init__(
        self,
        midnight_soc_log_path: str | None = "data/load-soc-baselines.csv",
        sample_buffer: "LoadSampleBuffer | None" = None,
    ) -> None:
        self.midnight_soc_log_path = Path(midnight_soc_log_path) if midnight_soc_log_path else None
        self._midnight_soc_by_day: dict[str, int] | None = None
        self.sample_buffer = sample_buffer

    def update(self, snapshot: SupervisorSnapshot) -> LoadSummary | None:
        current_a = estimate_load_current_a(snapshot)
        if current_a is None:
            return None

        voltage_v = load_voltage_v(snapshot)
        capacity_ah = bank_capacity_ah(snapshot)
        midnight_soc = self._midnight_soc_for_snapshot(snapshot)
        current_summary = LoadSummary(
            current_a=current_a,
            power_w=round(current_a * voltage_v),
        )
        rolling_average = None
        if self.sample_buffer is not None:
            self.sample_buffer.append(snapshot, current_summary)
            rolling_average = self.sample_buffer.rolling_average(
                now=snapshot.captured_at,
                window=ROLLING_LOAD_WINDOW,
            )
        summary = LoadSummary(
            current_a=current_a,
            power_w=current_summary.power_w,
            average_today_text=rolling_load_average_text(rolling_average),
            today_text=estimate_load_today_text(snapshot, capacity_ah, midnight_soc),
            remaining_text=estimate_load_remaining_from_average_a(
                snapshot,
                capacity_ah,
                None if rolling_average is None else rolling_average[0],
            ),
            rolling_average_a=None if rolling_average is None else rolling_average[0],
            rolling_average_w=None if rolling_average is None else rolling_average[1],
        )
        return summary

    def _midnight_soc_for_snapshot(self, snapshot: SupervisorSnapshot) -> int | None:
        if snapshot.battery is None or snapshot.battery.state_of_charge is None:
            return None

        captured_at = snapshot.captured_at.astimezone()
        day = captured_at.date().isoformat()
        midnight_soc = self._read_midnight_soc_by_day().get(day)
        if midnight_soc is not None:
            return midnight_soc

        if _seconds_since_midnight(captured_at) <= 300:
            midnight_soc = snapshot.battery.state_of_charge.soc_percent
            self._write_midnight_soc(day, captured_at, midnight_soc)
            return midnight_soc
        return None

    def _read_midnight_soc_by_day(self) -> dict[str, int]:
        if self._midnight_soc_by_day is not None:
            return self._midnight_soc_by_day

        self._midnight_soc_by_day = {}
        if self.midnight_soc_log_path is None or not self.midnight_soc_log_path.exists():
            return self._midnight_soc_by_day

        with self.midnight_soc_log_path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    self._midnight_soc_by_day[row["day"]] = int(row["soc_percent"])
                except (KeyError, ValueError):
                    continue
        return self._midnight_soc_by_day

    def _write_midnight_soc(self, day: str, captured_at: datetime, soc_percent: int) -> None:
        if self.midnight_soc_log_path is None:
            return

        baselines = self._read_midnight_soc_by_day()
        if day in baselines:
            return

        self.midnight_soc_log_path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not self.midnight_soc_log_path.exists() or self.midnight_soc_log_path.stat().st_size == 0
        with self.midnight_soc_log_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if needs_header:
                writer.writerow(["day", "captured_at", "soc_percent"])
            writer.writerow([day, captured_at.isoformat(), soc_percent])
        baselines[day] = soc_percent


class LoadSampleBuffer:
    FIELDNAMES = ["captured_at", "current_a", "power_w", "soc_percent", "voltage_v"]

    def __init__(
        self,
        path: str | None = "data/load-samples.csv",
        retention: timedelta = timedelta(hours=24),
        prune_interval: timedelta = timedelta(minutes=5),
    ) -> None:
        self.path = Path(path) if path else None
        self.retention = retention
        self.prune_interval = prune_interval
        self._last_prune_at: datetime | None = None
        self._lock = Lock()

    def append(self, snapshot: SupervisorSnapshot, summary: LoadSummary) -> None:
        if self.path is None:
            return

        sample = LoadSample(
            captured_at=snapshot.captured_at.astimezone(),
            current_a=summary.current_a,
            power_w=summary.power_w,
            soc_percent=_snapshot_soc_percent(snapshot),
            voltage_v=load_voltage_v(snapshot),
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            needs_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
                if needs_header:
                    writer.writeheader()
                writer.writerow(self._sample_row(sample))
            if self._should_prune(sample.captured_at):
                self._prune_locked(sample.captured_at)

    def samples(self, now: datetime | None = None, window: timedelta | None = None) -> list[LoadSample]:
        if self.path is None or not self.path.exists():
            return []

        reference = (now or datetime.now().astimezone()).astimezone()
        cutoff = reference - (window if window is not None else self.retention)
        with self._lock:
            return [sample for sample in self._read_samples_locked() if sample.captured_at >= cutoff]

    def rolling_average(self, now: datetime | None = None, window: timedelta = timedelta(hours=1)) -> tuple[float, float] | None:
        samples = self.samples(now=now, window=window)
        if not samples:
            return None
        average_a = sum(sample.current_a for sample in samples) / len(samples)
        average_w = sum(sample.power_w for sample in samples) / len(samples)
        return average_a, average_w

    def prune(self, now: datetime | None = None) -> None:
        if self.path is None:
            return
        reference = (now or datetime.now().astimezone()).astimezone()
        with self._lock:
            self._prune_locked(reference)

    def _should_prune(self, captured_at: datetime) -> bool:
        if self._last_prune_at is None:
            return True
        return captured_at - self._last_prune_at >= self.prune_interval

    def _prune_locked(self, now: datetime) -> None:
        if self.path is None or not self.path.exists():
            return

        cutoff = now - self.retention
        samples = [sample for sample in self._read_samples_locked() if sample.captured_at >= cutoff]
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
            writer.writeheader()
            for sample in samples:
                writer.writerow(self._sample_row(sample))
        self._last_prune_at = now

    def _read_samples_locked(self) -> list[LoadSample]:
        if self.path is None or not self.path.exists():
            return []

        samples: list[LoadSample] = []
        with self.path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                sample = self._sample_from_row(row)
                if sample is not None:
                    samples.append(sample)
        return samples

    def _sample_row(self, sample: LoadSample) -> dict[str, str]:
        return {
            "captured_at": sample.captured_at.isoformat(),
            "current_a": f"{sample.current_a:.3f}",
            "power_w": str(sample.power_w),
            "soc_percent": "" if sample.soc_percent is None else str(sample.soc_percent),
            "voltage_v": "" if sample.voltage_v is None else f"{sample.voltage_v:.3f}",
        }

    def _sample_from_row(self, row: dict[str, str]) -> LoadSample | None:
        try:
            captured_at = datetime.fromisoformat(row["captured_at"]).astimezone()
            soc_text = row.get("soc_percent", "")
            voltage_text = row.get("voltage_v", "")
            return LoadSample(
                captured_at=captured_at,
                current_a=float(row["current_a"]),
                power_w=int(row["power_w"]),
                soc_percent=None if not soc_text else int(soc_text),
                voltage_v=None if not voltage_text else float(voltage_text),
            )
        except (KeyError, TypeError, ValueError):
            return None


def estimate_load_current_a(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.classic is None or snapshot.battery is None or snapshot.battery.measurements is None:
        return None
    # Classic current is charger output. BMS current is net battery current,
    # positive while charging and negative while discharging.
    return snapshot.classic.battery_current_a - snapshot.battery.measurements.current_a


def load_voltage_v(snapshot: SupervisorSnapshot) -> float:
    if snapshot.battery is not None and snapshot.battery.measurements is not None:
        return snapshot.battery.measurements.voltage_v
    if snapshot.classic is not None:
        return snapshot.classic.battery_voltage_v
    return 0.0


def _snapshot_soc_percent(snapshot: SupervisorSnapshot) -> int | None:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return None
    return snapshot.battery.state_of_charge.soc_percent


def load_today_text(today_ah: float, bank_percent: float | None) -> str:
    text = f"{today_ah:.1f}Ah"
    if bank_percent is not None:
        text += f" {bank_percent:.1f}% of bank"
    return text


def estimate_load_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if today_ah is None:
        return MIDNIGHT_SOC_UNAVAILABLE
    return load_today_text(today_ah, today_ah / bank_capacity * 100)


def estimate_load_today_ah(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> float | None:
    if (
        snapshot.classic is None
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None
    if midnight_soc_percent is None:
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    battery_delta_ah = (current_soc_percent - midnight_soc_percent) / 100 * bank_capacity
    return snapshot.classic.daily_amp_hours_ah - battery_delta_ah


def estimate_load_average_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str | None:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if today_ah is None:
        return None

    elapsed_hours = _seconds_since_midnight(snapshot.captured_at.astimezone()) / 3600
    if elapsed_hours <= 0:
        return None

    average_a = today_ah / elapsed_hours
    average_w = round(average_a * load_voltage_v(snapshot))
    return f"{average_a:.1f}A  {average_w}W"


def estimate_load_remaining_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str | None:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if (
        today_ah is None
        or today_ah <= 0
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None

    elapsed_hours = _seconds_since_midnight(snapshot.captured_at.astimezone()) / 3600
    if elapsed_hours <= 0:
        return None

    average_load_a = today_ah / elapsed_hours
    if average_load_a <= 0:
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    remaining_ah = current_soc_percent / 100 * bank_capacity
    remaining_hours = remaining_ah / average_load_a
    return f"{remaining_hours:.1f}h"


def rolling_load_average_text(rolling_average: tuple[float, float] | None) -> str | None:
    if rolling_average is None:
        return None
    average_a, average_w = rolling_average
    return f"{average_a:.1f}A  {round(average_w)}W"


def estimate_load_remaining_from_average_a(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    average_load_a: float | None,
) -> str | None:
    if (
        average_load_a is None
        or average_load_a <= 0
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    remaining_ah = current_soc_percent / 100 * bank_capacity
    remaining_hours = remaining_ah / average_load_a
    return f"{remaining_hours:.1f}h"


def bank_capacity_ah(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.battery is None or snapshot.battery.extended_measurements is None:
        return None
    return snapshot.battery.extended_measurements.installed_capacity_ah


def _seconds_since_midnight(value: datetime) -> float:
    return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000


def _charge_controller_sections(snapshot: SupervisorSnapshot) -> list[str]:
    lines: list[str] = []
    controllers = [(0, snapshot.classic)]
    for index, classic in controllers:
        lines.extend([f"<h2>Charge Controller {index}</h2>", "<table>"])
        if classic is None:
            lines.append(_row("State", "No data"))
        else:
            lines.extend(
                [
                    _row(
                        "PV",
                        f"{classic.pv_voltage_v:.1f}V  {classic.pv_current_a:.1f}A  Voc {classic.last_voc_v:.1f}V",
                    ),
                    _row(
                        "Output",
                        f"{classic.battery_voltage_v:.1f}V  {classic.battery_current_a:.1f}A  {classic.battery_power_w}W",
                    ),
                    _row("Charge Status", _stage_value(classic.charge_stage, classic.state)),
                    *(
                        [
                            _row(
                                "PV input",
                                f"HyperVOC protection  Last Voc {classic.last_voc_v:.1f}V  High {classic.highest_input_voltage_v:.1f}V",
                            )
                        ]
                        if classic.is_hypervoc
                        else []
                    ),
                    _row("Production Today", f"{classic.daily_energy_kwh:.1f}kWh  {classic.daily_amp_hours_ah}Ah"),
                ]
            )
            if index == 0 and snapshot.classic_settings is not None:
                settings = snapshot.classic_settings
                lines.append(
                    _row(
                        "Charge Settings",
                        f"Limit {settings.battery_current_limit_a:.1f}A  "
                        f"Absorb {settings.absorb_voltage_v:.1f}V for {settings.absorb_time_s}s  "
                        f"Float {settings.float_voltage_v:.1f}V  "
                        f"EQ {settings.equalize_voltage_v:.1f}V",
                    )
                )
        lines.append("</table>")
    return lines


def _inverter_charger_section(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["<h2>Inverter/Charger</h2>", "<table>"]
    inv = snapshot.magnum
    if inv is None:
        lines.append(_row("State", "No data"))
        lines.append("</table>")
        return lines

    lines.append(_row("DC", f"{inv.dc_volts:.1f}V  {inv.dc_amps}A  {inv.dc_power_w}W"))

    ac_out_parts = [f"{inv.ac_volts_out}V"]
    if inv.ac_amps_out is not None:
        ac_out_parts.append(f"{inv.ac_amps_out}A")
    if inv.ac_freq_hz is not None:
        ac_out_parts.append(f"{inv.ac_freq_hz:.1f}Hz")
    lines.append(_row("AC Output", "  ".join(ac_out_parts)))

    if inv.ac_volts_in > 0:
        ac_in_parts = [f"{inv.ac_volts_in}V"]
        if inv.ac_amps_in is not None:
            ac_in_parts.append(f"{inv.ac_amps_in}A")
        lines.append(_row("AC Input", "  ".join(ac_in_parts)))
    else:
        lines.append(_row("AC Input", "0V  no source"))

    status_text = inv.status_label()
    fault = inv.fault_label()
    if fault:
        status_text += f"  Fault: {fault}"
    lines.append(_row("Status", status_text))

    settings_parts = []
    if inv.absorb_v is not None:
        settings_parts.append(f"Absorb {inv.absorb_v:.1f}V")
    if inv.float_v is not None:
        settings_parts.append(f"Float {inv.float_v:.1f}V")
    if inv.absorb_time_hr is not None:
        settings_parts.append(f"{inv.absorb_time_hr:.1f}hr")
    if inv.shore_amps is not None:
        settings_parts.append(f"Shore {inv.shore_amps}A")
    if inv.charger_amps_pct is not None and inv.charger_amps_pct > 0:
        settings_parts.append(f"Charger {inv.charger_amps_pct}%")
    if settings_parts:
        lines.append(_row("Settings", "  ".join(settings_parts)))

    lines.append("</table>")
    return lines


def _load_section(load_summary: LoadSummary | None) -> list[str]:
    lines = ["<h2>Load</h2>", "<table>"]
    if load_summary is None:
        lines.append(_row("Now", "No data"))
    else:
        lines.append(_row("Now", f"{load_summary.current_a:.1f}A  {load_summary.power_w}W"))
        if load_summary.average_today_text is not None:
            lines.append(_row("3hr Rolling Avg", load_summary.average_today_text))
        if load_summary.today_text is not None:
            lines.append(_row("Cumulative Today", load_summary.today_text))
        if load_summary.remaining_text is not None:
            lines.append(_row("Estimated Autonomy", load_summary.remaining_text))
    lines.append("</table>")
    return lines


def _battery_section(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["<h2>Battery Bank</h2>", "<table>"]
    battery = snapshot.battery
    if battery is None:
        for label, value in _missing_battery_rows(snapshot):
            lines.append(_row(label, value))
        lines.append("</table>")
        return lines

    if battery.measurements is not None:
        measurements = battery.measurements
        power_w = round(measurements.voltage_v * measurements.current_a)
        lines.append(_row("Flow", f"{measurements.voltage_v:.2f}V  {measurements.current_a:.1f}A  {power_w}W  {_battery_state(measurements.current_a)}"))
    if (
        battery.extended_measurements is not None
        and battery.extended_measurements.min_cell_voltage_v is not None
        and battery.extended_measurements.max_cell_voltage_v is not None
    ):
        extended = battery.extended_measurements
        delta_mv = round((extended.max_cell_voltage_v - extended.min_cell_voltage_v) * 1000)
        min_location = format_cell_location_for_display(extended.min_cell_location_text())
        max_location = format_cell_location_for_display(extended.max_cell_location_text())
        value = (
            f"Δ {delta_mv}mV; "
            f"min {min_location} {extended.min_cell_voltage_v:.3f}V; "
            f"max {max_location} {extended.max_cell_voltage_v:.3f}V"
        )
        lines.append(_row("Cells", value))
    if battery.status is not None:
        status = battery.status
        active_conditions = [*status.protection_flags, *status.alarm_flags]
        if active_conditions:
            lines.append(_row("Protection/Alarms", ", ".join(active_conditions)))
        else:
            lines.append(_row("Protection/Alarms", "none"))
    if battery.request_flags is not None:
        flags = battery.request_flags
        charge = "yes" if flags.charge_enable else "no"
        discharge = "yes" if flags.discharge_enable else "no"
        extra_requests = []
        if flags.force_charge_1 or flags.force_charge_2:
            extra_requests.append("force charge")
        if flags.full_charge_request:
            extra_requests.append("full charge")
        suffix = f"  Request: {', '.join(extra_requests)}" if extra_requests else ""
        lines.append(_row("Enable", f"charge {charge}  discharge {discharge}{suffix}"))
    if battery.charge_limits is not None:
        limits = battery.charge_limits
        lines.append(
            _row(
                "Limits",
                f"charge {limits.charge_voltage_limit_v:.1f}V/{limits.charge_current_limit_a:.1f}A  "
                f"discharge {limits.discharge_current_limit_a:.1f}A",
            )
        )
    lines.append("</table>")
    return lines


def _temperature_section(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["<h2>Temperatures</h2>", "<table>"]
    if (
        snapshot.battery is not None
        and snapshot.battery.extended_measurements is not None
        and snapshot.battery.extended_measurements.min_cell_temperature_c is not None
        and snapshot.battery.extended_measurements.max_cell_temperature_c is not None
    ):
        extended = snapshot.battery.extended_measurements
        lines.append(_row("Battery cells", f"{extended.min_cell_temperature_c:.1f}-{extended.max_cell_temperature_c:.1f}C"))
    if snapshot.classic is not None:
        classic = snapshot.classic
        lines.append(_row("Battery terminal", f"{classic.battery_temp_c:.1f}C"))
        lines.append(_row("CC0 FET", f"{classic.fet_temp_c:.1f}C"))
        lines.append(_row("CC0 PCB", f"{classic.pcb_temp_c:.1f}C"))
    if snapshot.magnum is not None:
        inv = snapshot.magnum
        lines.append(_row("INV battery", f"{inv.battery_temp_c}C"))
        lines.append(_row("INV transformer", f"{inv.transformer_temp_c}C"))
        lines.append(_row("INV FET", f"{inv.fet_temp_c}C"))
    if snapshot.ambient is None:
        lines.append(_row("Sensor 0 ambient", "disconnected"))
    else:
        lines.append(_row("Sensor 0 ambient", f"{snapshot.ambient.temperature_c:.1f}C"))
        if snapshot.ambient.humidity_percent is not None:
            lines.append(_row("Humidity", f"{snapshot.ambient.humidity_percent:.1f}%"))
    lines.append("</table>")
    return lines


def _missing_battery_rows(snapshot: SupervisorSnapshot) -> list[tuple[str, str]]:
    if snapshot.battery_can_health is None:
        return [("State", "No CAN data")]
    if snapshot.battery_can_health.dfu_devices:
        devices = []
        for device in snapshot.battery_can_health.dfu_devices[:2]:
            product = device.product or "STM32 DFU"
            serial = f" serial {device.serial}" if device.serial else ""
            devices.append(f"{product}{serial}")
        return [
            ("CAN adapter", "DFU/bootloader mode"),
            ("DFU devices", "; ".join(devices)),
            ("Action", "replug USB-CAN adapter without BOOT/DFU pressed"),
        ]
    if not snapshot.battery_can_health.socketcan_present:
        return [("CAN adapter", f"interface {snapshot.battery_can_health.interface} not present")]
    return [("State", "No CAN frames received")]


def render_snapshot_unavailable(exc: Exception, refresh_seconds: int = 10) -> str:
    return "\n".join(
        [
            "<!doctype html>",
            "<html>",
            "<head>",
            '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
            f'<meta http-equiv="refresh" content="{refresh_seconds}">',
            "<title>Off-Grid Power</title>",
            "<style>",
            "body{font-family:serif;color:#000;background:#fff;margin:8px;font-size:16px;-webkit-text-size-adjust:100%;text-size-adjust:100%;}",
            ".small{font-size:12px;}",
            "</style>",
            "</head>",
            "<body>",
            "<h2>Snapshot unavailable</h2>",
            f"<p>{escape(str(exc))}</p>",
            f'<p class="small">Retrying every {refresh_seconds} seconds.</p>',
            "</body>",
            "</html>",
        ]
    )


def _row(label: str, value: str, css_class: str = "") -> str:
    class_attr = f' class="{css_class}"' if css_class else ""
    return f"<tr><td>{escape(label)}</td><td{class_attr}>{escape(value)}</td></tr>"


def _battery_state(current_a: float) -> str:
    if current_a > BATTERY_IDLE_CURRENT_A:
        return "charging"
    if current_a < -BATTERY_IDLE_CURRENT_A:
        return "discharging"
    return "idle"


def _stage_value(charge_stage: str, state: str) -> str:
    stage_value = f"Stage: {charge_stage}"
    if state == charge_stage:
        return stage_value
    return f"{stage_value}  State: {state}"


def _status_text(snapshot: SupervisorSnapshot, status: str) -> str:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return f"Status: {status}"
    return f"SOC: {snapshot.battery.state_of_charge.soc_percent}%  Status: {status}"


def _soc_text(snapshot: SupervisorSnapshot) -> str:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return "SOC --"
    return f"{snapshot.battery.state_of_charge.soc_percent}%"


def _row_lines(label: str, values: list[str]) -> str:
    escaped_values = "<br>".join(escape(value) for value in values)
    return f"<tr><td>{escape(label)}</td><td>{escaped_values}</td></tr>"
