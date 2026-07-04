from __future__ import annotations

import json
import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.weather import (
    WeatherConfig,
    WeatherReport,
    WeatherService,
    add_moon_phase,
    aurora_likelihood_text,
    kp_forecast_entries,
    moon_phase_name,
    nearest_aurora_coordinate,
    normalize_longitude,
    tonight_window_from_weather,
    weather_api_payload,
    wind_compass,
)


class WeatherTest(unittest.TestCase):
    def test_adds_moon_phase_to_daily_forecast(self) -> None:
        data = {"daily": {"time": ["2026-06-06", "2026-06-07"]}}

        add_moon_phase(data)

        phases = data["daily"]["moon_phase"]
        self.assertEqual(len(phases), 2)
        self.assertTrue(all(0 <= phase < 1 for phase in phases))

    def test_finds_nearest_aurora_grid_point_with_wrapped_longitude(self) -> None:
        coordinates = [
            [275, 48, 12],
            [0, 0, 1],
            [180, 80, 5],
        ]

        nearest = nearest_aurora_coordinate(coordinates, latitude=47.9, longitude=-84.8)

        self.assertEqual(nearest, (-85.0, 48.0, 12.0))

    def test_normalizes_longitude(self) -> None:
        self.assertEqual(normalize_longitude(275), -85)
        self.assertEqual(normalize_longitude(-181), 179)

    def test_builds_tonight_window_from_sunset_to_next_sunrise(self) -> None:
        weather = {"daily": {"sunset": ["2026-06-06T21:34"], "sunrise": ["2026-06-06T05:41", "2026-06-07T05:40"]}}

        window = tonight_window_from_weather(weather)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertLess(window[0], window[1])
        self.assertEqual(window[0].strftime("%H:%M"), "21:34")
        self.assertEqual(window[1].strftime("%H:%M"), "05:40")

    def test_parses_kp_forecast_rows_as_local_times(self) -> None:
        rows = [{"time_tag": "2026-06-07T03:00:00", "kp": 5.33, "observed": "predicted", "noaa_scale": "G1"}]

        entries = kp_forecast_entries(rows)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kp"], 5.33)
        self.assertEqual(entries[0]["noaa_scale"], "G1")
        self.assertIsInstance(entries[0]["time"], datetime)

    def test_maps_kp_to_likelihood(self) -> None:
        self.assertEqual(aurora_likelihood_text(2.33), "unlikely")
        self.assertEqual(aurora_likelihood_text(4.0), "watch")
        self.assertEqual(aurora_likelihood_text(5.0), "possible")
        self.assertEqual(aurora_likelihood_text(7.0), "likely")


class WeatherDerivationsTest(unittest.TestCase):
    def test_wind_compass_octants(self) -> None:
        self.assertEqual(wind_compass(0), "N")
        self.assertEqual(wind_compass(225), "SW")
        self.assertEqual(wind_compass(359), "N")
        self.assertIsNone(wind_compass(None))
        self.assertIsNone(wind_compass("x"))

    def test_moon_phase_name(self) -> None:
        self.assertEqual(moon_phase_name(0.0), "new")
        self.assertEqual(moon_phase_name(0.5), "full")
        self.assertEqual(moon_phase_name(0.72), "last quarter")
        self.assertEqual(moon_phase_name(0.92), "waning crescent")
        self.assertIsNone(moon_phase_name(None))


class WeatherApiPayloadTest(unittest.TestCase):
    def _report(self) -> WeatherReport:
        return WeatherReport(
            label="Cabin",
            fetched_at=datetime.fromisoformat("2026-06-13T08:30:00-04:00"),
            data={
                "current": {
                    "weather_code": 3,
                    "temperature_2m": 11.0,
                    "wind_speed_10m": 5,
                    "wind_gusts_10m": 20,
                    "wind_direction_10m": 225,
                    "shortwave_radiation": 156,
                },
                "hourly": {
                    "time": ["2026-06-13T08:00"],
                    "weather_code": [45],
                    "temperature_2m": [10.2],
                    "precipitation_probability": [24],
                    "wind_speed_10m": [3],
                },
                "daily": {
                    "time": ["2026-06-13"],
                    "weather_code": [61],
                    "temperature_2m_min": [7.8],
                    "temperature_2m_max": [13.1],
                    "sunrise": ["2026-06-13T05:39"],
                    "sunset": ["2026-06-13T21:39"],
                    "moon_phase": [0.92],
                },
                "aurora": {
                    "probability_percent": 0,
                    "forecast_time": "2026-06-13T09:25",
                    "tonight": {"peak_kp": 3.7, "likelihood": "unlikely", "peak_time": "2026-06-13T23:00"},
                },
            },
        )

    def test_normalizes_to_source_agnostic_schema(self) -> None:
        payload = weather_api_payload(self._report())

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["label"], "Cabin")
        self.assertFalse(payload["stale"])
        # Units carried in keys; derivations (code text, compass) applied once.
        self.assertEqual(payload["current"]["temperature_c"], 11.0)
        self.assertEqual(payload["current"]["condition"], {"code": 3, "text": "overcast"})
        self.assertEqual(payload["current"]["wind"]["compass"], "SW")
        self.assertEqual(payload["current"]["irradiance"]["ghi_wm2"], 156.0)
        # Hourly/daily are lists of records, not parallel arrays.
        self.assertEqual(payload["hourly"][0]["at"], "2026-06-13T08:00")
        self.assertEqual(payload["hourly"][0]["condition"]["text"], "fog")
        self.assertEqual(payload["daily"][0]["low_c"], 7.8)
        self.assertEqual(payload["daily"][0]["high_c"], 13.1)
        self.assertEqual(payload["astronomy"]["moon"]["name"], "waning crescent")
        self.assertEqual(payload["astronomy"]["aurora"]["tonight"]["peak_kp"], 3.7)
        # No OpenMeteo field names leak through.
        self.assertNotIn("temperature_2m", json.dumps(payload))

    def test_irradiance_clamps_negative_noise_to_zero(self) -> None:
        # Open-Meteo can emit slightly-negative direct radiation in fog
        # (direct = GHI - diffuse). Irradiance is non-negative; clamp it.
        report = self._report()
        report.data["current"]["direct_radiation"] = -1.0
        report.data["current"]["shortwave_radiation"] = 170.0

        irradiance = weather_api_payload(report)["current"]["irradiance"]

        self.assertEqual(irradiance["direct_wm2"], 0.0)
        self.assertEqual(irradiance["ghi_wm2"], 170.0)

    def test_missing_report_is_unavailable_envelope(self) -> None:
        payload = weather_api_payload(None)

        self.assertTrue(payload["stale"])
        self.assertIsNone(payload["current"])
        self.assertEqual(payload["hourly"], [])
        self.assertEqual(payload["error"], "weather unavailable")

    def test_empty_data_keeps_envelope_without_sections(self) -> None:
        report = WeatherReport(label="Cabin", fetched_at=datetime.fromisoformat("2026-06-13T08:30:00-04:00"), data={}, stale=True)
        payload = weather_api_payload(report)

        self.assertIsNone(payload["current"])
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["label"], "Cabin")


class WeatherServiceRefreshTest(unittest.TestCase):
    def test_get_cached_returns_refreshing_placeholder_without_fetching(self) -> None:
        service = WeatherService(WeatherConfig(latitude=1.0, longitude=2.0, label="X"))
        calls = []

        def fake_fetch(reference, cached):
            calls.append(reference)
            return WeatherReport(label="X", fetched_at=reference, data={})

        service._fetch_and_store = fake_fetch

        report = service.get_cached(now=datetime.fromisoformat("2026-06-13T08:30:00-04:00"))

        self.assertEqual(calls, [])
        self.assertEqual(report.label, "X")
        self.assertTrue(report.stale)
        self.assertEqual(report.error, "weather unavailable")

    def test_request_refresh_fetches_in_background_without_blocking(self) -> None:
        service = WeatherService(WeatherConfig(latitude=1.0, longitude=2.0, label="X"))
        done = threading.Event()
        calls = []

        def fake_fetch(reference, cached):
            calls.append(reference)
            done.set()
            return WeatherReport(label="X", fetched_at=reference, data={})

        service._fetch_and_store = fake_fetch
        service.request_refresh()  # fire-and-forget

        self.assertTrue(done.wait(timeout=2.0))
        self.assertEqual(len(calls), 1)
        # The in-flight guard clears so a later refresh can run again.
        deadline = threading.Event()
        deadline.wait(0.05)
        self.assertFalse(service._refreshing)

    def test_request_refresh_does_not_duplicate_in_flight_fetch(self) -> None:
        service = WeatherService(WeatherConfig(latitude=1.0, longitude=2.0, label="X"))
        release = threading.Event()
        calls = []

        def slow_fetch(reference, cached):
            calls.append(reference)
            release.wait(timeout=2.0)
            return WeatherReport(label="X", fetched_at=reference, data={})

        service._fetch_and_store = slow_fetch
        service.request_refresh()
        service.request_refresh()  # second call while first is in flight: ignored
        release.set()

        deadline = threading.Event()
        deadline.wait(0.1)
        self.assertEqual(len(calls), 1)

    def test_request_refresh_if_needed_skips_fresh_cache(self) -> None:
        service = WeatherService(WeatherConfig(latitude=1.0, longitude=2.0, label="X"))
        fetched_at = datetime.fromisoformat("2026-06-13T08:30:00-04:00")
        service._report = WeatherReport(
            label="X",
            fetched_at=fetched_at,
            data={
                "current": {
                    "temperature_2m": 12.0,
                    "cloud_cover": 20,
                    "shortwave_radiation": 1,
                    "direct_radiation": 1,
                    "diffuse_radiation": 1,
                    "direct_normal_irradiance": 1,
                },
                "daily": {
                    "sunrise": ["2026-06-13T05:30"],
                    "sunset": ["2026-06-13T21:30"],
                    "moon_phase": [0.25],
                },
                "aurora": {"tonight": {"peak_kp": 1.0}},
            },
        )
        calls = []

        def fake_fetch(reference, cached):
            calls.append(reference)
            return service._report

        service._fetch_and_store = fake_fetch

        service.request_refresh_if_needed(now=fetched_at)

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
