from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.api_terminal_display import (
    _allocation_lines,
    _energy_text,
    render_api_snapshot,
    render_api_unavailable,
    render_api_weather,
)
from offgrid_power.weather import WeatherReport, weather_api_payload


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
                    "device": {"vendor": "MidNite", "model": "Classic 200"},
                    "conditions": [],
                    "pv_voltage_v": 91.2,
                    "pv_current_a": 4.5,
                    "last_voc_v": 101.0,
                    "battery_voltage_v": 54.8,
                    "battery_current_a": 7.1,
                    "battery_power_w": 389,
                    "charge_stage": {"canonical": "Float", "vendor": None},
                    "state": "MPPT or regulating voltage",
                    "daily_energy_kwh": 5.8,
                    "daily_amp_hours_ah": 106,
                    "temperatures_c": {"battery": 17.0, "fet": 31.0, "pcb": 29.0},
                    "settings": {
                        "current_limit_a": 80.0,
                        "absorb_voltage_v": 55.6,
                        "absorb_time_minutes": 32.5,
                        "float_voltage_v": 55.0,
                        "equalize_voltage_v": 55.6,
                    },
                },
                {
                    "id": "epever.1",
                    "device": {"vendor": "EPEver", "model": "TEP10425"},
                    "conditions": [],
                    "pv_voltage_v": 0.0,
                    "pv_current_a": 0.0,
                    "pv_power_w": 0,
                    "battery_voltage_v": 53.2,
                    "battery_current_a": 0.0,
                    "battery_power_w": 0,
                    "charge_stage": {"canonical": "Resting", "vendor": "No charging"},
                    "state": None,
                    "daily_energy_kwh": 0.1,
                    "temperatures_c": {"battery": 0.0, "device": 0.0},
                    "settings": {
                        "battery_type": "User",
                        "boost_voltage_v": 54.7,
                        "absorb_time_minutes": 120,
                        "equalize_voltage_v": 54.7,
                        "float_voltage_v": 53.6,
                        "max_charging_current_a": 80.0,
                        "low_voltage_disconnect_v": 49.7,
                    },
                },
            ],
            "inverter": {
                "dc_volts": 53.2,
                "dc_amps": 4,
                "dc_power_w": 213,
                "ac_volts_out": 120,
                "ac_amps_out": 1,
                "ac_freq_hz": 60.0,
                "ac_volts_in": 0,
                "ac_amps_in": 0,
                "status_label": "Inverting",
                "fault": "NONE",
                "battery_temp_c": 25,
                "transformer_temp_c": 37,
                "fet_temp_c": 30,
                "settings": {
                    "absorb_v": 54.4,
                    "float_v": 54.4,
                    "absorb_time_hr": 3.0,
                    "shore_amps": 30,
                    "charger_amps_pct": 0,
                },
            },
            "load": {"current_a": 4.0, "power_w": 212, "remaining_text": "46.0h"},
            "ambient": {"temperature_c": 18.2, "humidity_percent": None},
        }

        rendered = render_api_snapshot(payload, now=datetime(2026, 6, 5, 12, 0, 2, tzinfo=timezone.utc))

        self.assertIn("SOC:  92%  Status:  OK", rendered)
        self.assertIn("Now                   4.0A  212W", rendered)
        self.assertIn("Flow                  53.04V  -1.2A  -64W  discharging", rendered)
        self.assertIn("Cells                 Δ 6mV; min 2-14 3.312V; max 2-10 3.318V", rendered)
        self.assertIn("Charge Status         Stage: Float  State: MPPT or regulating voltage", rendered)
        # EPEver block: canonical first, vendor word in parens, no vendor knowledge in renderer.
        self.assertIn("Charge Status         Stage: Resting (No charging)", rendered)
        self.assertIn("Charge Settings       Limit 80.0A  Absorb 55.6V t=32.5m  Float 55.0V  EQ 55.6V", rendered)
        self.assertIn("Charge Controller 0 (MidNite Classic 200)\n", rendered)
        self.assertIn("\n\nCharge Controller 1 (EPEver TEP10425)\n", rendered)
        # cc1 mirrors cc0: a "Production Today" line (energy only, no Ah from the
        # EPEver), and no static "Rated" line. Adaptive units: sub-1 kWh shows Wh
        # (EPEver 0.1 -> 100Wh), >=1 kWh shows kWh (Classic 5.8 -> 5.8kWh).
        self.assertIn("Production Today      100Wh", rendered)
        self.assertIn("Production Today      5.8kWh  106Ah", rendered)
        self.assertNotIn("Rated", rendered)
        self.assertIn("Charge Settings       Limit 80.0A  Absorb 54.7V t=120m  Float 53.6V  EQ 54.7V", rendered)
        self.assertIn("\n\nInverter/Charger\n", rendered)
        self.assertIn("DC                    53.2V  4A  213W", rendered)
        self.assertIn("AC Output             120V  1A  60.0Hz", rendered)
        self.assertIn("Status                Inverting", rendered)
        self.assertIn("Charge Settings       Absorb 54.4V 3.0h  Float 54.4V  Shore 30A", rendered)
        self.assertNotIn("Temps", rendered)
        # "Battery terminal" and "INV battery" are suppressed (2026-06-17).
        self.assertNotIn("Battery terminal", rendered)
        self.assertNotIn("INV battery", rendered)
        self.assertIn("CC0 FET               31.0C", rendered)
        self.assertNotIn("CC1 battery", rendered)
        self.assertNotIn("CC1 device", rendered)
        self.assertIn("INV FET               30C", rendered)
        self.assertIn("Sensor 0 ambient temp 18.2C", rendered)
        self.assertNotIn("Press Ctrl-C", rendered)

    def test_renders_charge_allocation_section(self) -> None:
        lines = _allocation_lines(
            {
                "mode": "live",
                "reason": "BMS CCL fraction",
                "bms_ccl_a": 100.0,
                "charge_ceiling_a": 21.0,
                "budget_a": 22.0,
                "battery_current_a": 5.0,
                "battery_charge_a": 5.0,
                "load_allowance_a": 6.0,
                "weight_basis": "pv_power",
                "targets": {
                    "classic": {
                        "target_a": 11.0,
                        "disable": False,
                        "should_write": True,
                        "reason": "BMS CCL fraction",
                    },
                    "epever": {
                        "target_a": 0.0,
                        "disable": True,
                        "should_write": True,
                        "reason": "BMS CCL fraction",
                    },
                },
            }
        )
        rendered = "\n".join(lines)

        self.assertIn("Charge Allocation", rendered)
        self.assertIn("Limit                 21A net (CCL taper; BMS CCL 100A)", rendered)
        self.assertIn("Budget                22A  includes load 6A", rendered)
        self.assertIn("split by pv_power", rendered)
        self.assertIn("Classic               11.0A limited  *", rendered)
        self.assertIn("Epever                off  *", rendered)

    def test_allocation_section_renders_per_controller_release_state(self) -> None:
        lines = _allocation_lines(
            {
                "mode": "live",
                "reason": "unconstrained",
                "bms_ccl_a": 200.0,
                "charge_ceiling_a": None,
                "budget_a": 200.0,
                "battery_current_a": -3.0,
                "battery_charge_a": 5.0,
                "load_allowance_a": 4.0,
                "weight_basis": "pv_power",
                "targets": {
                    "classic": {
                        "target_a": 100.0,
                        "disable": False,
                        "should_write": False,
                        "reason": "unconstrained",
                    },
                    "epever": {
                        "target_a": 100.0,
                        "disable": False,
                        "should_write": True,
                        "reason": "charger inactive",
                    },
                },
            }
        )
        rendered = "\n".join(lines)

        self.assertIn("Limit                 not limiting (BMS CCL 200A)", rendered)
        self.assertIn("Budget                200A  split by pv_power", rendered)
        self.assertNotIn("battery -3A", rendered)
        self.assertIn("Classic               100.0A released", rendered)
        self.assertIn("Epever                100.0A released  *", rendered)

    def test_allocation_section_explains_feedback_clamp(self) -> None:
        lines = _allocation_lines(
            {
                "mode": "live",
                "reason": "feedback_clamp",
                "bms_ccl_a": 40.0,
                "charge_ceiling_a": 20.0,
                "budget_a": 1.0,
                "battery_current_a": 46.0,
                "battery_charge_a": 46.0,
                "load_allowance_a": 12.0,
                "weight_basis": "pv_power",
                "targets": {},
            }
        )

        rendered = "\n".join(lines)

        self.assertIn("Limit                 20A net (CCL taper, feedback clamp; BMS CCL 40A)", rendered)
        self.assertIn("Budget                1A  feedback: battery +46A > ceiling 20A  split by pv_power", rendered)

    def test_allocation_section_names_low_temperature_stop(self) -> None:
        lines = _allocation_lines(
            {
                "mode": "live",
                "reason": "battery temp -0.2C <= 0.0C",
                "bms_ccl_a": 200.0,
                "charge_ceiling_a": 0.0,
                "budget_a": 0.0,
                "battery_current_a": 0.0,
                "load_allowance_a": 4.0,
                "targets": {},
            }
        )

        self.assertIn(
            "Limit                 stop (low-temperature stop; BMS CCL 200A)",
            "\n".join(lines),
        )

    def test_allocation_section_marks_dry_run_mode(self) -> None:
        lines = _allocation_lines(
            {
                "mode": "dry-run",
                "reason": "BMS CCL fraction",
                "bms_ccl_a": 100.0,
                "allowance_a": 50.0,
                "budget_a": 49.0,
                "battery_current_a": 0.0,
                "load_allowance_a": 4.0,
                "targets": {},
            }
        )

        self.assertIn("Limit                 dry-run: 50A net (CCL taper; BMS CCL 100A)", "\n".join(lines))

    def test_allocation_section_precedes_temperatures_when_present(self) -> None:
        payload = {
            "captured_at": "2026-06-05T12:00:00+00:00",
            "status": {"ok": True, "severity": "OK", "errors": [], "conditions": []},
            "battery": {"soc_percent": 90, "voltage_v": 53.0, "current_a": 1.0},
            "allocation": {
                "mode": "live",
                "reason": "normal_load_allowance",
                "bms_ccl_a": 100.0,
                "charge_ceiling_a": None,
                "budget_a": 50.0,
                "battery_current_a": 1.0,
                "battery_charge_a": 1.0,
                "load_allowance_a": 4.0,
                "weight_basis": "pv_power",
                "targets": {"classic": {"target_a": 25.0, "disable": False, "should_write": False}},
            },
            "ambient": {"temperature_c": 18.2},
        }
        rendered = render_api_snapshot(payload, now=datetime(2026, 6, 5, 12, 0, 2, tzinfo=timezone.utc))
        self.assertIn("Charge Allocation", rendered)
        self.assertLess(rendered.index("Charge Allocation"), rendered.index("Temperatures"))

    def test_energy_text_switches_units_at_one_kwh(self) -> None:
        self.assertEqual(_energy_text(0.01), "10Wh")
        self.assertEqual(_energy_text(0.23), "230Wh")
        self.assertEqual(_energy_text(0.999), "999Wh")
        self.assertEqual(_energy_text(1.0), "1.0kWh")
        self.assertEqual(_energy_text(3.52), "3.5kWh")
        self.assertEqual(_energy_text(None), "?")

    def test_render_api_unavailable(self) -> None:
        rendered = render_api_unavailable("connection refused")

        self.assertIn("Status:  UNAVAILABLE", rendered)
        self.assertIn("connection refused", rendered)
        self.assertNotIn("Press Ctrl-C", rendered)


class ApiWeatherDisplayTest(unittest.TestCase):
    # Feed a provider-shaped WeatherReport through the normalizer so this
    # exercises the full service->renderer chain, not a hand-built schema.
    def _report(self, stale: bool = False) -> WeatherReport:
        return WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 13, 8, 30, tzinfo=timezone(timedelta(hours=-4))),
            stale=stale,
            data={
                "current": {
                    "weather_code": 3,
                    "temperature_2m": 11.0,
                    "apparent_temperature": 10.3,
                    "relative_humidity_2m": 94,
                    "cloud_cover": 94,
                    "wind_speed_10m": 5,
                    "wind_gusts_10m": 20,
                    "wind_direction_10m": 225,
                    "precipitation": 0.0,
                    "rain": 0.0,
                    "snowfall": 0.0,
                    "shortwave_radiation": 156,
                    "direct_radiation": 0,
                    "diffuse_radiation": 156,
                    "direct_normal_irradiance": 0,
                },
                "hourly": {
                    "time": ["2026-06-13T08:00", "2026-06-13T09:00"],
                    "weather_code": [3, 3],
                    "temperature_2m": [10.2, 11.7],
                    "precipitation_probability": [24, 15],
                    "wind_speed_10m": [3, 10],
                },
                "daily": {
                    "time": ["2026-06-13", "2026-06-14"],
                    "weather_code": [45, 3],
                    "temperature_2m_min": [7.8, 7.3],
                    "temperature_2m_max": [13.1, 10.0],
                    "precipitation_probability_max": [24, 8],
                    "precipitation_sum": [0.0, 0.0],
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

    def test_render_api_weather_sections(self) -> None:
        rendered = render_api_weather(weather_api_payload(self._report()))

        self.assertIn("Off-Grid Weather - Cabin", rendered)
        self.assertIn("As of: 08:30", rendered)
        self.assertIn("\nCurrent\n", rendered)
        self.assertIn("Condition             overcast", rendered)
        self.assertIn("Temperature           11.0C", rendered)
        self.assertIn("Wind                  5km/h  20km/h gust  SW", rendered)
        self.assertIn("\nNext Hours\n", rendered)
        self.assertIn("\nForecast\n", rendered)
        self.assertIn("\nSolar Irradiance\n", rendered)
        self.assertIn("Global Horizontal     156W/m2", rendered)
        self.assertIn("\nAstronomy\n", rendered)
        self.assertIn("Sun                   rise 05:39  set 21:39", rendered)
        self.assertIn("Moon                  waning crescent (0.92)", rendered)
        self.assertIn("Aurora Tonight        unlikely  peak Kp 3.7 around 23:00", rendered)

    def test_render_api_weather_no_data(self) -> None:
        rendered = render_api_weather(weather_api_payload(None))

        self.assertIn("Off-Grid Weather", rendered)
        self.assertIn("Weather unavailable", rendered)
        self.assertIn("Note: weather unavailable", rendered)

    def test_render_api_weather_marks_stale(self) -> None:
        rendered = render_api_weather(weather_api_payload(self._report(stale=True)))

        self.assertIn("Using last cached weather", rendered)


if __name__ == "__main__":
    unittest.main()
