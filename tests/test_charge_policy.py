from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import PylonChargeLimits
from offgrid_power.charge_policy import (
    ClassicChargeTargets,
    planned_classic_settings,
    validate_classic_targets_against_bms,
)
from offgrid_power.classic import ClassicChargeSettings


class ChargePolicyTest(unittest.TestCase):
    def test_planned_settings_fill_unspecified_values_from_current_settings(self) -> None:
        planned = planned_classic_settings(
            self._settings(),
            ClassicChargeTargets(absorb_voltage_v=55.6, absorb_time_s=1950),
        )

        self.assertEqual(planned.battery_current_limit_a, 80.0)
        self.assertEqual(planned.absorb_voltage_v, 55.6)
        self.assertEqual(planned.float_voltage_v, 54.0)
        self.assertEqual(planned.equalize_voltage_v, 55.2)
        self.assertEqual(planned.absorb_time_s, 1950)
        self.assertEqual(planned.max_temp_comp_voltage_v, 55.2)

    def test_validate_rejects_settings_above_bms_cvl_and_ccl(self) -> None:
        violations = validate_classic_targets_against_bms(
            ClassicChargeTargets(
                battery_current_limit_a=80.0,
                absorb_voltage_v=56.0,
                float_voltage_v=55.9,
                equalize_voltage_v=56.0,
                max_temp_comp_voltage_v=56.0,
            ),
            PylonChargeLimits(
                charge_voltage_limit_v=55.8,
                charge_current_limit_a=40.0,
                discharge_current_limit_a=200.0,
                discharge_voltage_limit_v=44.8,
            ),
        )

        self.assertIn("Absorb voltage 56.0V exceeds BMS CVL 55.8V", violations)
        self.assertIn("Float voltage 55.9V exceeds BMS CVL 55.8V", violations)
        self.assertIn("Equalize voltage 56.0V exceeds BMS CVL 55.8V", violations)
        self.assertIn("Max temp-comp voltage 56.0V exceeds BMS CVL 55.8V", violations)
        self.assertIn("Battery current limit 80.0A exceeds BMS CCL 40.0A", violations)

    def test_validate_allows_settings_within_bms_limits(self) -> None:
        violations = validate_classic_targets_against_bms(
            ClassicChargeTargets(
                battery_current_limit_a=40.0,
                absorb_voltage_v=55.2,
                float_voltage_v=54.0,
                equalize_voltage_v=55.2,
                max_temp_comp_voltage_v=55.2,
            ),
            PylonChargeLimits(
                charge_voltage_limit_v=58.4,
                charge_current_limit_a=40.0,
                discharge_current_limit_a=200.0,
                discharge_voltage_limit_v=44.8,
            ),
        )

        self.assertEqual(violations, [])

    def _settings(self) -> ClassicChargeSettings:
        return ClassicChargeSettings(
            captured_at=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            battery_current_limit_a=80.0,
            absorb_voltage_v=55.2,
            float_voltage_v=54.0,
            equalize_voltage_v=55.2,
            sliding_current_limit_a=800,
            absorb_time_s=300,
            max_temp_comp_voltage_v=55.2,
            min_temp_comp_voltage_v=52.8,
            temp_comp_mv_per_c_cell=-5.0,
            mppt_mode_raw=0x000B,
            aux_function_word=0x5201,
        )


if __name__ == "__main__":
    unittest.main()
