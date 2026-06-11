from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.api_terminal_display import render_api_snapshot, render_api_unavailable


class ApiTerminalDisplayTest(unittest.TestCase):
    def test_render_api_snapshot_uses_json_payload_without_hardware_objects(self) -> None:
        payload = {
            "schema_version": 1,
            "site_id": "cabin",
            "captured_at": "2026-06-05T12:00:00+00:00",
            "status": {"ok": True, "severity": "OK", "errors": [], "conditions": []},
            "battery": {
                "soc_percent": 92,
                "voltage_v": 53.04,
                "current_a": -1.2,
                "power_w": -63.6,
                "cell_min_v": 3.312,
                "cell_max_v": 3.318,
                "cell_delta_mv": 6,
                "cell_min_location": "02:14",
                "cell_max_location": "02:10",
                "charge_enabled": True,
                "discharge_enabled": True,
                "charge_voltage_limit_v": 58.4,
                "charge_current_limit_a": 200.0,
                "discharge_current_limit_a": 200.0,
                "protection_flags": [],
                "alarm_flags": [],
            },
            "solar": [
                {
                    "id": "classic.0",
                    "pv_voltage_v": 91.2,
                    "pv_current_a": 4.5,
                    "last_voc_v": 101.0,
                    "battery_voltage_v": 54.8,
                    "battery_current_a": 7.1,
                    "battery_power_w": 389,
                    "charge_stage": "Float",
                    "state": "MPPT or regulating voltage",
                    "daily_energy_kwh": 5.8,
                    "daily_amp_hours_ah": 106,
                    "temperatures_c": {"battery": 17.0, "fet": 31.0, "pcb": 29.0},
                    "settings": {
                        "current_limit_a": 80.0,
                        "absorb_voltage_v": 55.6,
                        "absorb_time_s": 1950,
                        "float_voltage_v": 55.0,
                        "equalize_voltage_v": 55.6,
                    },
                },
                {
                    "id": "epever.1",
                    "pv_voltage_v": 0.0,
                    "pv_current_a": 0.0,
                    "pv_power_w": 0,
                    "battery_voltage_v": 53.2,
                    "battery_current_a": 0.0,
                    "battery_power_w": 0,
                    "charge_stage": "No charging",
                    "state": "No charging",
                    "rated_pv_voltage_v": 250.0,
                    "rated_charging_current_a": 100.0,
                    "temperatures_c": {"battery": 0.0, "device": 0.0},
                    "settings": {
                        "battery_type": "User",
                        "boost_voltage_v": 54.7,
                        "float_voltage_v": 53.6,
                        "low_voltage_disconnect_v": 49.7,
                    },
                },
            ],
            "load": {"current_a": 4.0, "power_w": 212, "remaining_text": "46.0h"},
            "ambient": {"temperature_c": 18.2, "humidity_percent": None},
        }

        rendered = render_api_snapshot(payload, now=datetime(2026, 6, 5, 12, 0, 2, tzinfo=timezone.utc))

        self.assertIn("SOC:  92%  Status:  OK", rendered)
        self.assertIn("Now                   4.0A  212W", rendered)
        self.assertIn("Flow                  53.04V  -1.2A  -64W  discharging", rendered)
        self.assertIn("Cells                 Δ 6mV; min 2|14 3.312V; max 2|10 3.318V", rendered)
        self.assertIn("Charge Status         Stage: Float  State: MPPT or regulating voltage", rendered)
        self.assertIn("Charge Settings       Limit 80.0A  Absorb 55.6V 0.5h  Float 55.0V  EQ 55.6V", rendered)
        self.assertIn("Charge Controller 0 (Classic)\n", rendered)
        self.assertIn("\n\nCharge Controller 1 (Epever)\n", rendered)
        self.assertIn("Rated                 250V PV  100A charge", rendered)
        self.assertIn("Charge Settings       Type User  Boost 54.7V  Float 53.6V  LVD 49.7V", rendered)
        self.assertNotIn("Temps", rendered)
        self.assertIn("Battery terminal      17.0C", rendered)
        self.assertIn("CC0 FET               31.0C", rendered)
        self.assertIn("Sensor 0 ambient temp 18.2C", rendered)
        self.assertNotIn("Press Ctrl-C", rendered)

    def test_render_api_unavailable(self) -> None:
        rendered = render_api_unavailable("connection refused")

        self.assertIn("Status:  UNAVAILABLE", rendered)
        self.assertIn("connection refused", rendered)
        self.assertNotIn("Press Ctrl-C", rendered)


if __name__ == "__main__":
    unittest.main()
