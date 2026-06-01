from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.ambient import AmbientDs18b20Client, AmbientProbeDisconnected, AmbientTelemetry
from offgrid_power.canbus import CanFrame, PylonCanSnapshot, PylonStatus, decode_pylon_snapshot
from offgrid_power.classic import ClassicChargeSettings, ClassicTelemetry
from offgrid_power.supervisor import Supervisor
from offgrid_power.terminal_display import (
    CHANGED_DIGIT_END,
    CHANGED_DIGIT_START,
    DOWN_ARROW,
    UP_ARROW,
    highlight_changed_digits,
    render_snapshot,
)
from offgrid_power.web_display import HouseholdLoadSummary


class FakeClassicClient:
    def read(self):
        raise RuntimeError("not connected in test")


class FakeAmbientClient:
    def read(self) -> AmbientTelemetry:
        return AmbientTelemetry(
            temperature_c=21.5,
            humidity_percent=44.0,
            captured_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
        )


class FakeDisconnectedAmbientClient:
    def read(self) -> AmbientTelemetry:
        raise AmbientProbeDisconnected("not connected in test")


class FakeBrokenAmbientClient:
    def read(self) -> AmbientTelemetry:
        raise RuntimeError("sensor bus failed in test")


class FakeBatteryCanClient:
    def read(self):
        return decode_pylon_snapshot(
            [
                CanFrame(0x351, bytes.fromhex("4802D007D007C001")),
                CanFrame(0x355, bytes.fromhex("1E00640000000000")),
                CanFrame(0x356, bytes.fromhex("7914000071000000")),
                CanFrame(0x359, bytes.fromhex("0000000002504E00")),
                CanFrame(0x35C, bytes.fromhex("C000000000000000")),
                CanFrame(0x373, bytes.fromhex("CA0CCF0C1B011C01")),
            ]
        )


class AmbientSupervisorTest(unittest.TestCase):
    def test_supervisor_includes_ambient_reading(self) -> None:
        supervisor = Supervisor(
            classic=FakeClassicClient(),
            ambient=FakeAmbientClient(),
        )

        snapshot = supervisor.read_snapshot()

        self.assertIsNotNone(snapshot.ambient)
        self.assertEqual(snapshot.ambient.temperature_c, 21.5)
        self.assertEqual(snapshot.ambient.humidity_percent, 44.0)
        self.assertIn("Classic read failed", snapshot.errors[0])

    def test_terminal_display_renders_ambient_reading(self) -> None:
        supervisor = Supervisor(
            classic=FakeClassicClient(),
            ambient=FakeAmbientClient(),
        )

        rendered = render_snapshot(supervisor.read_snapshot())

        self.assertIn("Temperature Probes", rendered)
        self.assertIn("Sensor 0 ambient temp", rendered)
        self.assertIn("21.5C", rendered)
        self.assertIn("44.0%", rendered)

    def test_terminal_display_renders_disconnected_ambient_probe(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertIn("Sensor 0 ambient temp: disconnected", rendered)

    def test_terminal_display_renders_battery_can_reading(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=FakeBatteryCanClient()).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertIn("Battery Bank", rendered)
        self.assertIn("SOC:  30%  Status:  OK", rendered)
        self.assertIn("52.41V", rendered)
        battery_group = rendered[rendered.index("Battery Bank") : rendered.index("Charge Controller 0")]
        self.assertNotIn("SOC", battery_group)
        self.assertNotIn("BMS modules", battery_group)
        self.assertNotIn("State:   SOC", rendered)
        self.assertNotIn("SOH", rendered)
        self.assertIn("charge 58.4V/200.0A", rendered)
        self.assertIn("charge yes  discharge yes", rendered)
        self.assertIn("Protection/Alarms:     none", rendered)
        self.assertIn("3.274-3.279V", rendered)

    def test_terminal_display_enumerates_battery_protections_and_alarms(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()
        snapshot = snapshot.__class__(
            captured_at=snapshot.captured_at,
            classic=None,
            classic_settings=None,
            battery=PylonCanSnapshot(
                status=PylonStatus(
                    module_count=2,
                    protection_flags=("high cell voltage", "low temperature"),
                    alarm_flags=("charge over current",),
                    manufacturer_marker="PN",
                )
            ),
            ambient=None,
            errors=[],
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("Status:  OK", rendered)
        self.assertNotIn("BMS modules", rendered)
        self.assertIn("Protection/Alarms:     high cell voltage, low temperature, charge over current", rendered)

    def test_terminal_display_renders_usage_remaining_line(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()

        rendered = render_snapshot(
            snapshot,
            household_load=HouseholdLoadSummary(
                current_a=5.1,
                power_w=272,
                average_today_text="4.3A  228W",
                today_text="104.0Ah",
                remaining_text="21.2h",
            ),
        )

        self.assertIn("Load", rendered)
        self.assertNotIn("Usage", rendered)
        self.assertNotIn("Household Usage", rendered)
        self.assertIn("  Now:                   5.1A  272W", rendered)
        self.assertIn("  Average Today:         4.3A  228W", rendered)
        self.assertIn("  Cumulative Today:      104.0Ah", rendered)
        self.assertIn("  Estimated Autonomy:    21.2h", rendered)
        rows = rendered.splitlines()
        now_line = next(line for line in rows if line.startswith("  Now:"))
        average_line = next(line for line in rows if line.startswith("  Average Today:"))
        cumulative_line = next(line for line in rows if line.startswith("  Cumulative Today:"))
        remaining_line = next(line for line in rows if line.startswith("  Estimated Autonomy:"))
        self.assertEqual(
            {now_line.index("5.1A"), average_line.index("4.3A"), cumulative_line.index("104.0Ah"), remaining_line.index("21.2h")},
            {25},
        )

    def test_supervisor_treats_disconnected_ambient_probe_as_non_error_state(self) -> None:
        snapshot = Supervisor(classic=None, ambient=FakeDisconnectedAmbientClient()).read_snapshot()

        self.assertTrue(snapshot.ok)
        self.assertIsNone(snapshot.ambient)
        self.assertEqual(snapshot.errors, [])

    def test_supervisor_treats_ambient_read_failures_as_non_error_state(self) -> None:
        snapshot = Supervisor(classic=None, ambient=FakeBrokenAmbientClient()).read_snapshot()

        self.assertTrue(snapshot.ok)
        self.assertIsNone(snapshot.ambient)
        self.assertEqual(snapshot.errors, [])

    def test_disconnected_ambient_probe_does_not_hide_classic_errors(self) -> None:
        snapshot = Supervisor(
            classic=FakeClassicClient(),
            ambient=FakeDisconnectedAmbientClient(),
        ).read_snapshot()

        self.assertFalse(snapshot.ok)
        self.assertIsNone(snapshot.ambient)
        self.assertEqual(len(snapshot.errors), 1)
        self.assertIn("Classic read failed", snapshot.errors[0])

    def test_terminal_display_renders_charge_controller_zero_with_pv_first(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()
        snapshot = snapshot.__class__(
            captured_at=snapshot.captured_at,
            classic=ClassicTelemetry(
                captured_at=snapshot.captured_at,
                battery_voltage_v=54.8,
                pv_voltage_v=91.2,
                battery_current_a=7.1,
                daily_energy_kwh=5.8,
                battery_power_w=389,
                charge_stage_code=5,
                charge_stage="Float",
                state_code=3,
                state="MPPT or regulating voltage",
                pv_current_a=4.5,
                last_voc_v=101.0,
                highest_input_voltage_v=110.0,
                daily_amp_hours_ah=106,
                lifetime_energy_kwh=1234,
                lifetime_amp_hours_ah=5678,
                info_flags=0,
                active_flags=["PV input lower than battery output"],
                battery_temp_c=17.0,
                fet_temp_c=31.0,
                pcb_temp_c=29.0,
            ),
            classic_settings=None,
            battery=FakeBatteryCanClient().read(),
            ambient=None,
            errors=[],
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("Charge Controller 0", rendered)
        self.assertNotIn("MidNite Classic", rendered)
        self.assertLess(rendered.index("Battery Bank"), rendered.index("Charge Controller 0"))
        self.assertLess(rendered.index("  PV:"), rendered.index("  Battery:"))
        self.assertIn("  Stage:                 Float  State: MPPT or regulating voltage", rendered)
        self.assertIn("  Today Cumulative:      5.8kWh  106Ah", rendered)
        self.assertNotIn("Flags:", rendered)
        self.assertNotIn("PV input lower than battery output", rendered)

    def test_terminal_display_renders_charge_settings_inside_charge_controller_group(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()
        snapshot = snapshot.__class__(
            captured_at=snapshot.captured_at,
            classic=ClassicTelemetry(
                captured_at=snapshot.captured_at,
                battery_voltage_v=54.8,
                pv_voltage_v=91.2,
                battery_current_a=7.1,
                daily_energy_kwh=5.8,
                battery_power_w=389,
                charge_stage_code=5,
                charge_stage="Float",
                state_code=5,
                state="Float",
                pv_current_a=4.5,
                last_voc_v=101.0,
                highest_input_voltage_v=110.0,
                daily_amp_hours_ah=106,
                lifetime_energy_kwh=1234,
                lifetime_amp_hours_ah=5678,
                info_flags=0,
                active_flags=[],
                battery_temp_c=17.0,
                fet_temp_c=31.0,
                pcb_temp_c=29.0,
            ),
            classic_settings=ClassicChargeSettings(
                captured_at=snapshot.captured_at,
                battery_current_limit_a=80.0,
                absorb_voltage_v=55.2,
                float_voltage_v=54.0,
                equalize_voltage_v=55.2,
                sliding_current_limit_a=0,
                absorb_time_s=300,
                max_temp_comp_voltage_v=56.0,
                min_temp_comp_voltage_v=52.0,
                temp_comp_mv_per_c_cell=0.0,
                mppt_mode_raw=0,
                aux_function_word=0,
            ),
            battery=None,
            ambient=None,
            errors=[],
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("  Charge Settings:       Limit 80.0A  Absorb 55.2V for 300s  Float 54.0V  EQ 55.2V", rendered)
        self.assertNotIn("Charge Controller 0 Settings", rendered)
        self.assertLess(rendered.index("Charge Controller 0"), rendered.index("  Charge Settings:"))
        self.assertLess(rendered.index("  Charge Settings:"), rendered.index("Temperature Probes"))

    def test_terminal_display_hides_redundant_charge_controller_state(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()
        snapshot = snapshot.__class__(
            captured_at=snapshot.captured_at,
            classic=ClassicTelemetry(
                captured_at=snapshot.captured_at,
                battery_voltage_v=52.9,
                pv_voltage_v=22.0,
                battery_current_a=0.0,
                daily_energy_kwh=5.9,
                battery_power_w=0,
                charge_stage_code=0,
                charge_stage="Resting",
                state_code=0,
                state="Resting",
                pv_current_a=0.0,
                last_voc_v=101.0,
                highest_input_voltage_v=110.0,
                daily_amp_hours_ah=108,
                lifetime_energy_kwh=1234,
                lifetime_amp_hours_ah=5678,
                info_flags=0,
                active_flags=[],
                battery_temp_c=16.6,
                fet_temp_c=26.7,
                pcb_temp_c=33.0,
            ),
            classic_settings=None,
            battery=None,
            ambient=None,
            errors=[],
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("  Stage:                 Resting", rendered)
        self.assertNotIn("State: Resting", rendered)

    def test_terminal_display_highlights_changed_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Sensor 0 ambient temp:  21.5C",
            current="Sensor 0 ambient temp:  23.5C",
        )

        self.assertIn(f"{CHANGED_DIGIT_START}23.5C{CHANGED_DIGIT_END}", highlighted)
        self.assertIn("21.5", highlight_changed_digits(None, "21.5"))

    def test_terminal_display_still_highlights_time_digits_only(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Local time: 2026-05-28 15:48:49 EDT",
            current="Local time: 2026-05-28 15:48:55 EDT",
        )

        self.assertIn(f":{CHANGED_DIGIT_START}5{CHANGED_DIGIT_END}{CHANGED_DIGIT_START}5{CHANGED_DIGIT_END} EDT", highlighted)

    def test_terminal_display_adds_direction_arrows_to_changed_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Battery:  54.2V    3.6A    196W\nLocal time: 2026-05-28 15:48:49 EDT",
            current="Battery:  54.1V    3.8A    190W\nLocal time: 2026-05-28 15:48:55 EDT",
        )

        self.assertIn(DOWN_ARROW, highlighted)
        self.assertIn(UP_ARROW, highlighted)
        self.assertEqual(highlighted.count(UP_ARROW) + highlighted.count(DOWN_ARROW), 3)

    def test_terminal_display_highlights_millivolt_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Cells: 3.399-3.414V (15mV delta)",
            current="Cells: 3.399-3.414V (16mV delta)",
        )

        self.assertIn(f"{CHANGED_DIGIT_START}16mV{CHANGED_DIGIT_END}", highlighted)
        self.assertIn(UP_ARROW, highlighted)

    def test_terminal_display_pads_unchanged_value_arrow_slots(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Battery:  54.2V    3.6A",
            current="Battery:  54.2V    3.8A",
        )

        self.assertIn("54.2V ", highlighted)
        self.assertIn(UP_ARROW, highlighted)

    def test_ds18b20_reads_sysfs_temperature(self) -> None:
        device_dir = REPO_ROOT / ".tmp-test-ds18b20" / "28-000001"
        device_dir.mkdir(parents=True, exist_ok=True)
        try:
            (device_dir / "w1_slave").write_text(
                "aa bb cc dd ee ff gg hh ii : crc=11 YES\n"
                "aa bb cc dd ee ff gg hh ii t=21562\n",
                encoding="utf-8",
            )

            telemetry = AmbientDs18b20Client(
                devices_path=str(device_dir.parent),
            ).read()

            self.assertEqual(telemetry.temperature_c, 21.562)
            self.assertIsNone(telemetry.humidity_percent)
        finally:
            (device_dir / "w1_slave").unlink(missing_ok=True)
            device_dir.rmdir()
            device_dir.parent.rmdir()

    def test_ds18b20_rejects_zero_temperature_as_disconnected(self) -> None:
        device_dir = REPO_ROOT / ".tmp-test-ds18b20-zero" / "28-000001"
        device_dir.mkdir(parents=True, exist_ok=True)
        try:
            (device_dir / "w1_slave").write_text(
                "aa bb cc dd ee ff gg hh ii : crc=11 YES\n"
                "aa bb cc dd ee ff gg hh ii t=0\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(AmbientProbeDisconnected, "probe may be disconnected"):
                AmbientDs18b20Client(
                    devices_path=str(device_dir.parent),
                ).read()
        finally:
            (device_dir / "w1_slave").unlink(missing_ok=True)
            device_dir.rmdir()
            device_dir.parent.rmdir()


if __name__ == "__main__":
    unittest.main()
