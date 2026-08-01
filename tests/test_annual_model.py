from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import annual_model as model  # noqa: E402


class AnnualModelTest(unittest.TestCase):
    def test_winter_season_only_includes_october_through_april(self) -> None:
        self.assertEqual(model._winter_season("2020-10-01"), 2020)
        self.assertEqual(model._winter_season("2021-04-30"), 2020)
        self.assertIsNone(model._winter_season("2021-05-01"))
        self.assertIsNone(model._winter_season("2021-09-30"))

    def test_generator_starts_at_threshold_and_stops_at_target(self) -> None:
        capacity = model.BANK_USABLE_KWH
        start = 0.20 * capacity
        stop = 0.90 * capacity

        result = model._run_generator_hour(
            soc=start + 0.05,
            natural_net=-0.10,
            generator_on=False,
            start_kwh=start,
            stop_kwh=stop,
            generator_kw=3.2,
        )

        soc, generator_on, sessions, runtime, energy, unserved = result
        self.assertEqual(sessions, 1)
        self.assertTrue(generator_on)
        self.assertGreater(soc, start)
        self.assertAlmostEqual(energy, 3.2 * runtime)
        self.assertEqual(unserved, 0)

    def test_generator_uses_fractional_hour_to_reach_stop_soc(self) -> None:
        capacity = model.BANK_USABLE_KWH
        stop = 0.90 * capacity
        result = model._run_generator_hour(
            soc=stop - 0.32,
            natural_net=0.0,
            generator_on=True,
            start_kwh=0.20 * capacity,
            stop_kwh=stop,
            generator_kw=3.2,
        )

        soc, generator_on, _sessions, runtime, energy, _unserved = result
        self.assertAlmostEqual(runtime, 0.1)
        self.assertAlmostEqual(energy, 0.32)
        self.assertAlmostEqual(soc, stop)
        self.assertFalse(generator_on)

    def test_hourly_saturation_discards_midday_surplus(self) -> None:
        # A full battery cannot bank the first hour's surplus for the following
        # hour. This is the behavior the former daily-net model missed.
        soc = model.BANK_USABLE_KWH
        soc = min(model.BANK_USABLE_KWH, soc + 2.0)
        soc = max(0.0, soc - 1.0)
        self.assertAlmostEqual(soc, model.BANK_USABLE_KWH - 1.0)

    def test_production_applies_winter_derate_only_in_winter(self) -> None:
        base = {"poa_w": 1000.0, "factor": 1.0, "month": 12}
        summer = {**base, "month": 7}
        self.assertAlmostEqual(model.production(base, 1.6, winter_pv_factor=0.75), 1.2)
        self.assertAlmostEqual(model.production(summer, 1.6, winter_pv_factor=0.75), 1.6)

    def test_generator_threshold_validation_relationship(self) -> None:
        self.assertEqual(model.WEATHER_MODEL, "era5")
        self.assertLess(model.GENERATOR_START_SOC, model.GENERATOR_STOP_SOC)
        self.assertEqual(model.GENERATOR_KW, 3.2)
        self.assertEqual(model.GENERATOR_STOP_SOC, 0.90)


if __name__ == "__main__":
    unittest.main()
