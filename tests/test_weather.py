from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.weather import (
    add_moon_phase,
    aurora_likelihood_text,
    kp_forecast_entries,
    nearest_aurora_coordinate,
    normalize_longitude,
    tonight_window_from_weather,
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


if __name__ == "__main__":
    unittest.main()
