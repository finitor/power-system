from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offgrid_power.api_terminal_display import (
    _allocation_lines,
    _energy_text,
    _solar_lines,
    render_api_snapshot,
    render_api_unavailable,
    render_api_weather,
)
from offgrid_power.weather import WeatherReport, weather_api_payload
from golden import check_golden


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
                    "device": {"vendor": "MidNite", "model": "Classic 200", "short_name": "Classic"},
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
                        "max_temp_comp_voltage_v": 56.8,
                    },
                },
                {
                    "id": "epever.1",
                    "device": {"vendor": "EPEver", "model": "TEP10425", "short_name": "Epever"},
                    "conditions": [],
                    "pv_voltage_v": 0.0,
                    "pv_current_a": 0.0,
                    "pv_power_w": 0,
                    "battery_voltage_v": 53.2,
                    "battery_current_a": 0.0,
                    "battery_power_w": 0,
                    "charge_stage": {"canonical": "Resting", "vendor": None},
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

        # Whole-frame layout (every field, spacing, ordering) lives in the golden
        # file; re-bless with UPDATE_GOLDEN=1 on an intended change.
        check_golden(self, "api_snapshot_full", rendered)

        # Deliberate *suppressions* are kept as explicit guards so a golden
        # re-bless can't silently reintroduce them:
        # - EPEver settings hide EQ; controllers carry no static "Rated" line.
        # - EPEver settings hide EQ; controllers carry no static "Rated" line.
        # - "INV battery" row suppressed (2026-06-17).
        self.assertNotIn("Rated", rendered)
        self.assertNotIn("EQ 54.7V", rendered)
        self.assertNotIn("INV battery", rendered)

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
                "weight_basis": "equal",
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
        self.assertIn("CC0 (Classic)         100%  11.0A limited  *", rendered)
        self.assertIn("CC1 (Epever)          0%  off  *", rendered)

    def test_epever_production_today_unavailable_message(self) -> None:
        lines = _solar_lines(
            [
                {
                    "id": "epever.1",
                    "device": {"vendor": "EPEver", "model": "TEP10425", "short_name": "Epever"},
                    "charge_stage": {"canonical": "Resting", "vendor": None},
                    "daily_energy_kwh": None,
                    "daily_energy_unavailable_reason": "unavailable, midnight cumulative energy was not logged",
                }
            ]
        )
        self.assertIn(
            "Production Today      unavailable, midnight cumulative energy was not logged",
            "\n".join(lines),
        )

    def test_allocation_limit_line_shows_ccl_scaling_factor(self) -> None:
        # Active taper: the fraction rides alongside the BMS CCL.
        active = _allocation_lines(
            {
                "mode": "live",
                "reason": "BMS CCL fraction",
                "ccl_scaling_factor": 0.6,
                "bms_ccl_a": 100.0,
                "charge_ceiling_a": 60.0,
                "budget_a": 60.0,
                "weight_basis": "equal",
                "targets": {"classic": {"target_a": 30.0, "disable": False, "reason": "BMS CCL fraction"}},
            }
        )
        self.assertIn("Limit                 60A net (CCL taper; BMS CCL 100A; scaling 60%)", "\n".join(active))

        # Unconstrained: still surfaced so a nudge can be confirmed off the knee.
        idle = _allocation_lines(
            {
                "mode": "live",
                "reason": "unconstrained",
                "ccl_scaling_factor": 0.6,
                "bms_ccl_a": 200.0,
                "charge_ceiling_a": None,
                "budget_a": 200.0,
                "targets": {"classic": {"target_a": 80.0, "disable": False, "reason": "unconstrained"}},
            }
        )
        self.assertIn("Limit                 not limiting (BMS CCL 200A; scaling 60%)", "\n".join(idle))

    def test_allocation_section_prefers_solar_short_names(self) -> None:
        lines = _allocation_lines(
            {
                "mode": "live",
                "reason": "unconstrained",
                "bms_ccl_a": 200.0,
                "budget_a": 180.0,
                "targets": {
                    "classic": {"target_a": 80.0, "disable": False, "reason": "unconstrained"},
                    "epever": {"target_a": 100.0, "disable": False, "reason": "unconstrained"},
                },
            },
            solar=[
                {"id": "classic.0", "device": {"short_name": "Classic"}},
                {"id": "epever.1", "device": {"short_name": "Epever"}},
            ],
        )
        rendered = "\n".join(lines)

        self.assertIn("CC0 (Classic)         44%  80.0A released", rendered)
        self.assertIn("CC1 (Epever)          56%  100.0A released", rendered)

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
                "weight_basis": "equal",
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
        self.assertIn("Budget                200A", rendered)
        self.assertNotIn("battery -3A", rendered)
        self.assertIn("CC0 (Classic)         100%  100.0A released", rendered)
        self.assertIn("CC1 (Epever)          0%  100.0A released  *", rendered)

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
                "weight_basis": "equal",
                "targets": {},
            }
        )

        rendered = "\n".join(lines)

        self.assertIn("Limit                 20A net (CCL taper, feedback clamp; BMS CCL 40A)", rendered)
        self.assertIn("Budget                1A  feedback: battery +46A > ceiling 20A", rendered)

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
            "solar": [{"id": "classic.0", "device": {"short_name": "Classic"}}],
            "allocation": {
                "mode": "live",
                "reason": "normal_load_allowance",
                "bms_ccl_a": 100.0,
                "charge_ceiling_a": None,
                "budget_a": 50.0,
                "battery_current_a": 1.0,
                "battery_charge_a": 1.0,
                "load_allowance_a": 4.0,
                "weight_basis": "equal",
                "targets": {"classic": {"target_a": 25.0, "disable": False, "should_write": False}},
            },
            "ambient": {"temperature_c": 18.2},
        }
        rendered = render_api_snapshot(payload, now=datetime(2026, 6, 5, 12, 0, 2, tzinfo=timezone.utc))
        self.assertIn("Charge Allocation", rendered)
        self.assertIn("CC0 (Classic)         25.0A limited", rendered)
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

        # Whole-frame layout in the golden file; re-bless with UPDATE_GOLDEN=1.
        check_golden(self, "api_weather_full", rendered)

    def test_render_api_weather_no_data(self) -> None:
        rendered = render_api_weather(weather_api_payload(None))

        self.assertIn("Off-Grid Weather", rendered)
        self.assertIn("Weather unavailable", rendered)
        self.assertIn("Note: weather unavailable", rendered)

    def test_render_api_weather_marks_stale(self) -> None:
        rendered = render_api_weather(weather_api_payload(self._report(stale=True)))

        self.assertIn("Using last cached weather", rendered)

    def test_render_api_weather_marks_too_stale_as_unreachable(self) -> None:
        report = self._report(stale=True)
        rendered = render_api_weather(weather_api_payload(report), now=report.fetched_at + timedelta(hours=1, minutes=1))

        self.assertIn("Weather service unreachable since", rendered)
        self.assertIn("Weather unavailable", rendered)
        self.assertNotIn("Current\n", rendered)


if __name__ == "__main__":
    unittest.main()
