"""Open-Meteo weather fetch and formatting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
NOAA_AURORA_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
NOAA_KP_FORECAST_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
REQUIRED_CURRENT_FIELDS = {
    "temperature_2m",
    "cloud_cover",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
}
CONTROL_WEATHER_MAX_AGE = timedelta(hours=1)


@dataclass(frozen=True)
class WeatherConfig:
    latitude: float
    longitude: float
    label: str = "Cabin"
    refresh: timedelta = timedelta(minutes=30)
    cache_path: str | None = None
    timeout_s: float = 8
    aurora_enabled: bool = True


@dataclass(frozen=True)
class WeatherReport:
    label: str
    fetched_at: datetime
    data: dict[str, Any]
    stale: bool = False
    error: str | None = None


class WeatherService:
    def __init__(self, config: WeatherConfig) -> None:
        self.config = config
        self._report: WeatherReport | None = None
        self._lock = Lock()
        self._refreshing = False

    def get(self, now: datetime | None = None) -> WeatherReport:
        reference = (now or datetime.now().astimezone()).astimezone()
        with self._lock:
            cached = self._report or self._read_cache()
            if (
                cached is not None
                and cached.label == self.config.label
                and report_has_required_current_fields(cached)
                and reference - cached.fetched_at.astimezone() < self.config.refresh
            ):
                self._report = cached
                return cached
        return self._fetch_and_store(reference, cached)

    def get_cached(self, now: datetime | None = None) -> WeatherReport:
        """Return the latest cached report without touching the network."""
        reference = (now or datetime.now().astimezone()).astimezone()
        with self._lock:
            cached = self._report or self._read_cache()
            if cached is not None:
                self._report = cached
                return cached
            refreshing = self._refreshing
        return WeatherReport(
            label=self.config.label,
            fetched_at=reference,
            data={},
            stale=True,
            error="weather refresh in progress" if refreshing else "weather unavailable",
        )

    def current_temperature_for_control(
        self,
        now: datetime | None = None,
        *,
        max_age: timedelta = CONTROL_WEATHER_MAX_AGE,
    ) -> float | None:
        """Return fresh outdoor temperature for a fail-off control consumer.

        Display weather may degrade gracefully to a stale disk cache. Heater
        control may not: cold modules raise VOC, so stale/missing temperature
        could make weak winter light look like strong solar input.
        """
        reference = (now or datetime.now().astimezone()).astimezone()
        report = self.get_cached(reference)
        if report.stale or report.error:
            return None
        age = reference.astimezone(timezone.utc) - report.fetched_at.astimezone(timezone.utc)
        if age < timedelta(0) or age > max_age:
            return None
        current = weather_api_payload(report).get("current")
        if not current:
            return None
        return _wx_number(current.get("temperature_c"))

    def request_refresh_if_needed(self, now: datetime | None = None) -> None:
        """Start a background refresh only when cached weather is missing or stale."""
        reference = (now or datetime.now().astimezone()).astimezone()
        with self._lock:
            cached = self._report or self._read_cache()
            if (
                cached is not None
                and cached.label == self.config.label
                and report_has_required_current_fields(cached)
                and reference - cached.fetched_at.astimezone() < self.config.refresh
            ):
                self._report = cached
                return
        self.request_refresh()

    def request_refresh(self) -> None:
        """Fetch fresh weather in the background; the new report lands on a
        later get(). Fire-and-forget so a manual panel switch never blocks on
        the network. A refresh already in flight is not duplicated.
        """
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        Thread(target=self._refresh_worker, name="weather-refresh", daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            self._fetch_and_store(datetime.now().astimezone(), self._report)
        finally:
            with self._lock:
                self._refreshing = False

    def _fetch_and_store(self, reference: datetime, cached: WeatherReport | None) -> WeatherReport:
        try:
            data = fetch_open_meteo(self.config, timeout_s=self.config.timeout_s)
            if self.config.aurora_enabled:
                try:
                    data["aurora"] = fetch_noaa_aurora_probability(
                        self.config.latitude,
                        self.config.longitude,
                        timeout_s=self.config.timeout_s,
                    )
                    data["aurora"]["tonight"] = fetch_noaa_kp_tonight_forecast(data, timeout_s=self.config.timeout_s)
                except Exception as exc:  # noqa: BLE001 - aurora is advisory to the weather report.
                    data["aurora"] = {"error": str(exc)}
            report = WeatherReport(label=self.config.label, fetched_at=reference, data=data)
            self._write_cache(report)
            with self._lock:
                self._report = report
            return report
        except Exception as exc:  # noqa: BLE001 - weather is advisory and should degrade to stale cache.
            stale = cached or self._read_cache()
            if stale is not None:
                report = WeatherReport(
                    label=stale.label,
                    fetched_at=stale.fetched_at,
                    data=stale.data,
                    stale=True,
                    error=str(exc),
                )
                with self._lock:
                    self._report = report
                return report
            return WeatherReport(
                label=self.config.label,
                fetched_at=reference,
                data={},
                stale=True,
                error=str(exc),
            )

    def _read_cache(self) -> WeatherReport | None:
        if not self.config.cache_path:
            return None
        path = Path(self.config.cache_path)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return WeatherReport(
                label=str(payload["label"]),
                fetched_at=datetime.fromisoformat(payload["fetched_at"]),
                data=payload["data"],
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, report: WeatherReport) -> None:
        if not self.config.cache_path:
            return
        path = Path(self.config.cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "label": report.label,
            "fetched_at": report.fetched_at.isoformat(),
            "data": report.data,
        }
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def fetch_open_meteo(config: WeatherConfig, timeout_s: float = 8) -> dict[str, Any]:
    params = {
        "latitude": f"{config.latitude:.7f}",
        "longitude": f"{config.longitude:.7f}",
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "precipitation",
                "rain",
                "snowfall",
                "weather_code",
                "cloud_cover",
                "wind_speed_10m",
                "wind_gusts_10m",
                "wind_direction_10m",
                "shortwave_radiation",
                "direct_radiation",
                "diffuse_radiation",
                "direct_normal_irradiance",
            ]
        ),
        "hourly": ",".join(
            [
                "temperature_2m",
                "precipitation_probability",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
                "wind_gusts_10m",
                "shortwave_radiation",
                "direct_radiation",
                "diffuse_radiation",
                "direct_normal_irradiance",
            ]
        ),
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "precipitation_probability_max",
                "sunrise",
                "sunset",
            ]
        ),
        "forecast_days": "3",
        "forecast_hours": "8",
        "timezone": "auto",
        "wind_speed_unit": "kmh",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    }
    url = f"{OPEN_METEO_FORECAST_URL}?{urlencode(params)}"
    with urlopen(url, timeout=timeout_s) as response:
        if response.status != 200:
            raise RuntimeError(f"Open-Meteo returned HTTP {response.status}")
        data = json.loads(response.read().decode("utf-8"))
    add_moon_phase(data)
    return data


def add_moon_phase(data: dict[str, Any]) -> None:
    daily = data.get("daily")
    if not isinstance(daily, dict):
        return
    days = daily.get("time")
    if not isinstance(days, list):
        return
    daily["moon_phase"] = [moon_phase_for_date(day) for day in days]


def moon_phase_for_date(day: object) -> float | None:
    if not isinstance(day, str):
        return None
    try:
        date = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    known_new_moon = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    synodic_month_days = 29.53058867
    elapsed_days = (date - known_new_moon).total_seconds() / 86400
    return (elapsed_days % synodic_month_days) / synodic_month_days


def fetch_noaa_aurora_probability(latitude: float, longitude: float, timeout_s: float = 8) -> dict[str, Any]:
    with urlopen(NOAA_AURORA_URL, timeout=timeout_s) as response:
        if response.status != 200:
            raise RuntimeError(f"NOAA SWPC returned HTTP {response.status}")
        data = json.loads(response.read().decode("utf-8"))
    nearest = nearest_aurora_coordinate(data.get("coordinates") or [], latitude, longitude)
    payload = {
        "provider": "noaa-swpc-ovation",
        "observation_time": data.get("Observation Time"),
        "forecast_time": data.get("Forecast Time"),
    }
    if nearest is not None:
        lon, lat, probability = nearest
        payload.update(
            {
                "latitude": lat,
                "longitude": lon,
                "probability_percent": probability,
            }
        )
    return payload


def fetch_noaa_kp_tonight_forecast(weather_data: dict[str, Any], timeout_s: float = 8) -> dict[str, Any]:
    window = tonight_window_from_weather(weather_data)
    if window is None:
        return {"error": "sunset/sunrise unavailable"}
    with urlopen(NOAA_KP_FORECAST_URL, timeout=timeout_s) as response:
        if response.status != 200:
            raise RuntimeError(f"NOAA SWPC Kp forecast returned HTTP {response.status}")
        rows = json.loads(response.read().decode("utf-8"))
    start, end = window
    entries = [entry for entry in kp_forecast_entries(rows) if start <= entry["time"] < end]
    if not entries:
        return {
            "error": "no Kp forecast rows for tonight",
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }
    peak = max(entries, key=lambda entry: entry["kp"])
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "peak_kp": peak["kp"],
        "peak_time": peak["time"].isoformat(),
        "noaa_scale": peak.get("noaa_scale"),
        "likelihood": aurora_likelihood_text(peak["kp"]),
        "entries": [
            {
                "time": entry["time"].isoformat(),
                "kp": entry["kp"],
                "observed": entry.get("observed"),
                "noaa_scale": entry.get("noaa_scale"),
            }
            for entry in entries
        ],
    }


def tonight_window_from_weather(weather_data: dict[str, Any]) -> tuple[datetime, datetime] | None:
    daily = weather_data.get("daily")
    if not isinstance(daily, dict):
        return None
    sunsets = daily.get("sunset")
    sunrises = daily.get("sunrise")
    if not isinstance(sunsets, list) or not sunsets:
        return None
    if not isinstance(sunrises, list) or len(sunrises) < 2:
        return None
    try:
        start = datetime.fromisoformat(str(sunsets[0])).astimezone()
        end = datetime.fromisoformat(str(sunrises[1])).astimezone()
    except ValueError:
        return None
    if end <= start:
        return None
    return start, end


def kp_forecast_entries(rows: object) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    entries = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            time = datetime.fromisoformat(str(row["time_tag"])).replace(tzinfo=timezone.utc).astimezone()
            kp = float(row["kp"])
        except (KeyError, TypeError, ValueError):
            continue
        entries.append(
            {
                "time": time,
                "kp": kp,
                "observed": row.get("observed"),
                "noaa_scale": row.get("noaa_scale"),
            }
        )
    return entries


def aurora_likelihood_text(kp: float) -> str:
    if kp >= 7:
        return "likely"
    if kp >= 5:
        return "possible"
    if kp >= 4:
        return "watch"
    return "unlikely"


def nearest_aurora_coordinate(coordinates: list, latitude: float, longitude: float) -> tuple[float, float, float] | None:
    best = None
    best_distance = None
    normalized_longitude = normalize_longitude(longitude)
    for coordinate in coordinates:
        if not isinstance(coordinate, list) or len(coordinate) < 3:
            continue
        try:
            lon = normalize_longitude(float(coordinate[0]))
            lat = float(coordinate[1])
            probability = float(coordinate[2])
        except (TypeError, ValueError):
            continue
        lon_delta = abs(lon - normalized_longitude)
        lon_delta = min(lon_delta, 360 - lon_delta)
        distance = (lat - latitude) ** 2 + lon_delta**2
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = (lon, lat, probability)
    return best


def normalize_longitude(longitude: float) -> float:
    value = longitude
    while value > 180:
        value -= 360
    while value < -180:
        value += 360
    return value


def report_has_required_current_fields(report: WeatherReport) -> bool:
    current = report.data.get("current")
    if not isinstance(current, dict):
        return False
    daily = report.data.get("daily")
    if not isinstance(daily, dict):
        return False
    has_astronomy = all(isinstance(daily.get(name), list) and daily.get(name) for name in ["sunrise", "sunset", "moon_phase"])
    aurora = report.data.get("aurora")
    has_aurora_tonight = isinstance(aurora, dict) and isinstance(aurora.get("tonight"), dict)
    return REQUIRED_CURRENT_FIELDS.issubset(current) and has_astronomy and has_aurora_tonight


def weather_code_text(code: object) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "unknown"
    if value == 0:
        return "clear"
    if value in (1, 2):
        return "mainly clear"
    if value == 3:
        return "overcast"
    if value in (45, 48):
        return "fog"
    if value in (51, 53, 55, 56, 57):
        return "drizzle"
    if value in (61, 63, 65, 66, 67):
        return "rain"
    if value in (71, 73, 75, 77):
        return "snow"
    if value in (80, 81, 82):
        return "showers"
    if value in (85, 86):
        return "snow showers"
    if value in (95, 96, 99):
        return "thunderstorm"
    return f"code {value}"


# ---------------------------------------------------------------------------
# Source-agnostic normalization
#
# Everything below turns a provider-shaped WeatherReport (currently Open-Meteo
# + NOAA aurora) into a stable, vendor-neutral API payload. Renderers and the
# metrics recorder consume only this schema, so the provider's field names and
# the semantic derivations (weather-code text, wind compass, moon-phase name)
# live in exactly one place. Units are carried in the key suffixes.
# ---------------------------------------------------------------------------


def wind_compass(value: object) -> str | None:
    """8-point compass heading the wind blows *from*, or None if unparseable."""
    try:
        degrees = float(value)
    except (TypeError, ValueError):
        return None
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[int((degrees + 22.5) // 45) % 8]


def moon_phase_name(value: object) -> str | None:
    """Phase fraction (0=new, 0.5=full) to its conventional name, or None."""
    try:
        phase = float(value)
    except (TypeError, ValueError):
        return None
    if phase < 0.03 or phase > 0.97:
        return "new"
    if phase < 0.22:
        return "waxing crescent"
    if phase < 0.28:
        return "first quarter"
    if phase < 0.47:
        return "waxing gibbous"
    if phase < 0.53:
        return "full"
    if phase < 0.72:
        return "waning gibbous"
    if phase < 0.78:
        return "last quarter"
    return "waning crescent"


def _wx_number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _wx_irradiance(value: object) -> float | None:
    """Irradiance is physically non-negative; clamp to 0.

    Open-Meteo derives horizontal direct radiation as roughly GHI - diffuse,
    so in near-fully-diffuse conditions (fog) it can land a watt or two below
    zero from model/rounding noise. Negative irradiance is meaningless, so the
    normalized payload clamps it for every consumer (displays and the metrics
    recorder alike).
    """
    number = _wx_number(value)
    return None if number is None else max(0.0, number)


def _wx_indexed(values: object, index: int) -> object:
    if isinstance(values, list) and 0 <= index < len(values):
        return values[index]
    return None


def _wx_condition(code: object) -> dict:
    number = _wx_number(code)
    return {"code": int(number) if number is not None else None, "text": weather_code_text(code)}


def _wx_current(current: dict) -> dict:
    return {
        "temperature_c": _wx_number(current.get("temperature_2m")),
        "apparent_temperature_c": _wx_number(current.get("apparent_temperature")),
        "humidity_pct": _wx_number(current.get("relative_humidity_2m")),
        "cloud_cover_pct": _wx_number(current.get("cloud_cover")),
        "precipitation_mm": _wx_number(current.get("precipitation")),
        "rain_mm": _wx_number(current.get("rain")),
        "snowfall_cm": _wx_number(current.get("snowfall")),
        "condition": _wx_condition(current.get("weather_code")),
        "wind": {
            "speed_kmh": _wx_number(current.get("wind_speed_10m")),
            "gust_kmh": _wx_number(current.get("wind_gusts_10m")),
            "direction_deg": _wx_number(current.get("wind_direction_10m")),
            "compass": wind_compass(current.get("wind_direction_10m")),
        },
        "irradiance": {
            "ghi_wm2": _wx_irradiance(current.get("shortwave_radiation")),
            "direct_wm2": _wx_irradiance(current.get("direct_radiation")),
            "diffuse_wm2": _wx_irradiance(current.get("diffuse_radiation")),
            "dni_wm2": _wx_irradiance(current.get("direct_normal_irradiance")),
        },
    }


def _wx_hourly(hourly: dict) -> list[dict]:
    times = hourly.get("time") or []
    return [
        {
            "at": at,
            "condition": _wx_condition(_wx_indexed(hourly.get("weather_code"), index)),
            "temperature_c": _wx_number(_wx_indexed(hourly.get("temperature_2m"), index)),
            "precip_probability_pct": _wx_number(_wx_indexed(hourly.get("precipitation_probability"), index)),
            "wind_speed_kmh": _wx_number(_wx_indexed(hourly.get("wind_speed_10m"), index)),
        }
        for index, at in enumerate(times)
    ]


def _wx_daily(daily: dict) -> list[dict]:
    days = daily.get("time") or []
    return [
        {
            "date": date,
            "condition": _wx_condition(_wx_indexed(daily.get("weather_code"), index)),
            "low_c": _wx_number(_wx_indexed(daily.get("temperature_2m_min"), index)),
            "high_c": _wx_number(_wx_indexed(daily.get("temperature_2m_max"), index)),
            "precip_probability_pct": _wx_number(_wx_indexed(daily.get("precipitation_probability_max"), index)),
            "precip_sum_mm": _wx_number(_wx_indexed(daily.get("precipitation_sum"), index)),
        }
        for index, date in enumerate(days)
    ]


def _wx_aurora(aurora: object) -> dict | None:
    if not isinstance(aurora, dict) or aurora.get("error"):
        return None
    probability = _wx_number(aurora.get("probability_percent"))
    tonight_raw = aurora.get("tonight")
    tonight = None
    if isinstance(tonight_raw, dict) and not tonight_raw.get("error"):
        peak_kp = _wx_number(tonight_raw.get("peak_kp"))
        likelihood = tonight_raw.get("likelihood")
        if peak_kp is not None and isinstance(likelihood, str):
            tonight = {
                "peak_kp": peak_kp,
                "likelihood": likelihood,
                "peak_at": tonight_raw.get("peak_time"),
                "scale": tonight_raw.get("noaa_scale"),
            }
    if probability is None and tonight is None:
        return None
    return {
        "probability_pct": probability,
        "valid_at": aurora.get("forecast_time"),
        "tonight": tonight,
    }


def _wx_astronomy(daily: dict, aurora: object) -> dict:
    phase = _wx_indexed(daily.get("moon_phase"), 0)
    return {
        "sunrise": _wx_indexed(daily.get("sunrise"), 0),
        "sunset": _wx_indexed(daily.get("sunset"), 0),
        "moon": {"phase": _wx_number(phase), "name": moon_phase_name(phase)},
        "aurora": _wx_aurora(aurora),
    }


def weather_api_payload(report: "WeatherReport | None") -> dict:
    """Normalize a WeatherReport into the source-agnostic weather API schema.

    The envelope (label/observed_at/stale/error) always present; the data
    sections are None/empty when there is no usable forecast.
    """
    if report is None:
        return {
            "schema_version": 1,
            "label": None,
            "observed_at": None,
            "stale": True,
            "error": "weather unavailable",
            "current": None,
            "hourly": [],
            "daily": [],
            "astronomy": None,
        }
    data = report.data or {}
    if not data:
        return {
            "schema_version": 1,
            "label": report.label,
            "observed_at": report.fetched_at.isoformat(),
            "stale": report.stale,
            "error": report.error,
            "current": None,
            "hourly": [],
            "daily": [],
            "astronomy": None,
        }
    daily = data.get("daily") or {}
    return {
        "schema_version": 1,
        "label": report.label,
        "observed_at": report.fetched_at.isoformat(),
        "stale": report.stale,
        "error": report.error,
        "current": _wx_current(data.get("current") or {}),
        "hourly": _wx_hourly(data.get("hourly") or {}),
        "daily": _wx_daily(daily),
        "astronomy": _wx_astronomy(daily, data.get("aurora")),
    }
