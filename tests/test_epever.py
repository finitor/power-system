from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.epever import decode_settings, decode_telemetry


CAPTURED_AT = datetime(2026, 6, 11, 13, 55, tzinfo=timezone.utc)


class EpeverDecodeTest(unittest.TestCase):
    def test_decodes_live_probe_registers(self) -> None:
        telemetry = decode_telemetry(
            rated=[42, 4, 25000, 10000, 61248, 7, 4800, 10000, 61248],
            live=[0, 0, 0, 0, 5311, 0, 0, 0],
            temperatures=[0, 0],
            soc=[2055],
            status=[24576, 0],
            captured_at=CAPTURED_AT,
        )

        self.assertEqual(telemetry.captured_at, CAPTURED_AT)
        self.assertEqual(telemetry.pv_voltage_v, 0.0)
        self.assertEqual(telemetry.pv_current_a, 0.0)
        self.assertEqual(telemetry.pv_power_w, 0.0)
        self.assertEqual(telemetry.battery_voltage_v, 53.11)
        self.assertEqual(telemetry.battery_current_a, 0.0)
        self.assertEqual(telemetry.battery_power_w, 0)
        self.assertIsNone(telemetry.battery_soc_percent)
        self.assertEqual(telemetry.charging_status, "No charging")
        self.assertEqual(telemetry.rated_pv_voltage_v, 250.0)
        self.assertEqual(telemetry.rated_charging_current_a, 100.0)
        self.assertEqual(telemetry.rated_battery_voltage_v, 48.0)

    def test_decodes_battery_settings(self) -> None:
        settings = decode_settings(
            [6, 200, 300, 1, 60, 2, 4, 5470, 5360, 5360, 5330, 5330, 5000, 4970, 4800],
            captured_at=CAPTURED_AT,
        )

        self.assertEqual(settings.battery_type, "User")
        self.assertEqual(settings.battery_capacity_ah, 200)
        self.assertEqual(settings.boost_voltage_v, 54.7)
        self.assertEqual(settings.float_voltage_v, 53.6)
        self.assertEqual(settings.low_voltage_disconnect_v, 49.7)


if __name__ == "__main__":
    unittest.main()
