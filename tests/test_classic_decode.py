from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.classic import RegisterBlock, decode_live, decode_settings


class ClassicDecodeTest(unittest.TestCase):
    def test_decode_live_registers_from_observed_classic_sample(self) -> None:
        block = RegisterBlock(
            start_register=4115,
            values=[
                502, 1018, 16, 32, 80, 1027, 10, 1236, 1968, 0,
                60, 29317, 0, 52852, 0, 12292, 45568, 196, 463, 464,
            ],
        )

        telemetry = decode_live(
            block,
            captured_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        )

        self.assertEqual(telemetry.battery_voltage_v, 50.2)
        self.assertEqual(telemetry.pv_voltage_v, 101.8)
        self.assertEqual(telemetry.battery_current_a, 1.6)
        self.assertEqual(telemetry.battery_power_w, 80)
        self.assertEqual(telemetry.charge_stage_code, 4)
        self.assertEqual(telemetry.charge_stage, "BulkMppt")
        self.assertEqual(telemetry.state_code, 3)
        self.assertEqual(telemetry.state, "MPPT or regulating voltage")
        self.assertEqual(telemetry.battery_temp_c, 19.6)

    def test_decode_settings_registers_from_observed_classic_sample(self) -> None:
        block = RegisterBlock(
            start_register=4148,
            values=[
                400, 592, 540, 648, 400, 0, 7200, 648, 528,
                50, 0, 0, 0, 0, 3600, 30, 11, 20993,
            ],
        )

        settings = decode_settings(
            block,
            captured_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        )

        self.assertEqual(settings.battery_current_limit_a, 40.0)
        self.assertEqual(settings.absorb_voltage_v, 59.2)
        self.assertEqual(settings.float_voltage_v, 54.0)
        self.assertEqual(settings.equalize_voltage_v, 64.8)
        self.assertEqual(settings.absorb_time_s, 7200)
        self.assertEqual(settings.temp_comp_mv_per_c_cell, -5.0)


if __name__ == "__main__":
    unittest.main()
