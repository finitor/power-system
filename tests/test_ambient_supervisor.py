from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.ambient import AmbientDs18b20Client, AmbientProbeDisconnected, AmbientTelemetry
from offgrid_power.canbus import (
    CanBusHealth,
    CanFrame,
    PylonCanSnapshot,
    PylonChargeLimits,
    PylonExtendedMeasurements,
    PylonStatus,
    UsbDevice,
    decode_pylon_snapshot,
)
from offgrid_power.classic import ClassicChargeSettings, ClassicTelemetry
from offgrid_power.load import LoadTotals, LoadTotalsTracker
from offgrid_power.supervisor import Supervisor
from offgrid_power.terminal_display import (
    CHANGED_DIGIT_END,
    CHANGED_DIGIT_START,
    DOWN_ARROW,
    UP_ARROW,
    format_refresh_age,
    highlight_changed_digits,
    render_snapshot,
)
from offgrid_power.web_display import LoadSummary


class FakeClassicClient:
    def read(self):
        raise RuntimeError("not connected in test")


class FakeClassicLiveClient:
    def __init__(
        self,
        *,
        charge_stage_code: int = 4,
        charge_stage: str = "BulkMppt",
        state_code: int = 3,
        state: str = "MPPT or regulating voltage",
        active_flags: list[str] | None = None,
        last_voc_v: float = 110.0,
        highest_input_voltage_v: float = 120.0,
    ) -> None:
        self.charge_stage_code = charge_stage_code
        self.charge_stage = charge_stage
        self.state_code = state_code
        self.state = state
        self.active_flags = active_flags or ["Battery temperature sensor installed"]
        self.last_voc_v = last_voc_v
        self.highest_input_voltage_v = highest_input_voltage_v

    def read(self):
        return (
            ClassicTelemetry(
                captured_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
                battery_voltage_v=53.6,
                pv_voltage_v=100.0,
                battery_current_a=11.6,
                daily_energy_kwh=0.9,
                battery_power_w=625,
                charge_stage_code=self.charge_stage_code,
                charge_stage=self.charge_stage,
                state_code=self.state_code,
                state=self.state,
                pv_current_a=6.3,
                last_voc_v=self.last_voc_v,
                highest_input_voltage_v=self.highest_input_voltage_v,
                daily_amp_hours_ah=17,
                lifetime_energy_kwh=1000,
                lifetime_amp_hours_ah=2000,
                info_flags=0,
                active_flags=self.active_flags,
                battery_temp_c=15.3,
                fet_temp_c=47.8,
                pcb_temp_c=45.0,
            ),
            None,
        )


class FakeClassicSettingsClient:
    def read(self):
        captured_at = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
        return (
            ClassicTelemetry(
                captured_at=captured_at,
                battery_voltage_v=53.6,
                pv_voltage_v=100.0,
                battery_current_a=11.6,
                daily_energy_kwh=0.9,
                battery_power_w=625,
                charge_stage_code=4,
                charge_stage="BulkMppt",
                state_code=3,
                state="MPPT or regulating voltage",
                pv_current_a=6.3,
                last_voc_v=110.0,
                highest_input_voltage_v=120.0,
                daily_amp_hours_ah=17,
                lifetime_energy_kwh=1000,
                lifetime_amp_hours_ah=2000,
                info_flags=0,
                active_flags=[],
                battery_temp_c=15.3,
                fet_temp_c=47.8,
                pcb_temp_c=45.0,
            ),
            ClassicChargeSettings(
                captured_at=captured_at,
                battery_current_limit_a=80.0,
                absorb_voltage_v=56.0,
                float_voltage_v=55.9,
                equalize_voltage_v=56.0,
                sliding_current_limit_a=800,
                absorb_time_s=3600,
                max_temp_comp_voltage_v=56.0,
                min_temp_comp_voltage_v=52.8,
                temp_comp_mv_per_c_cell=-5.0,
                mppt_mode_raw=0x000B,
                aux_function_word=0x5201,
            ),
        )


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


class FakeLimitedBatteryCanClient:
    def read(self):
        return PylonCanSnapshot(
            charge_limits=PylonChargeLimits(
                charge_voltage_limit_v=55.8,
                charge_current_limit_a=40.0,
                discharge_current_limit_a=200.0,
                discharge_voltage_limit_v=44.8,
            )
        )


class FakeCellSequenceBatteryCanClient:
    def __init__(self, readings: list[tuple[float, float]]) -> None:
        self.readings = readings
        self.index = 0

    def read(self):
        min_cell_v, max_cell_v = self.readings[min(self.index, len(self.readings) - 1)]
        self.index += 1
        return PylonCanSnapshot(
            extended_measurements=PylonExtendedMeasurements(
                min_cell_voltage_v=min_cell_v,
                max_cell_voltage_v=max_cell_v,
            )
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

        self.assertIn("Temperatures", rendered)
        self.assertIn("Sensor 0 ambient temp", rendered)
        self.assertIn("21.5C", rendered)
        self.assertIn("44.0%", rendered)

    def test_terminal_display_renders_disconnected_ambient_probe(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertIn("Sensor 0 ambient temp: disconnected", rendered)

    def test_terminal_display_orders_core_groups(self) -> None:
        snapshot = Supervisor(
            classic=FakeClassicLiveClient(),
            ambient=FakeAmbientClient(),
            battery=FakeBatteryCanClient(),
        ).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertLess(rendered.index("Load"), rendered.index("Battery Bank"))
        self.assertLess(rendered.index("Battery Bank"), rendered.index("Charge Controller 0"))
        self.assertLess(rendered.index("Charge Controller 0"), rendered.index("Temperatures"))
        self.assertNotIn("MidNite Classic", rendered)
        self.assertNotIn("Classic Charge Settings", rendered)
        self.assertNotIn("Flags:", rendered)
        self.assertIn("Battery terminal:      15.3C", rendered)
        self.assertIn("Charge controller FET: 47.8C", rendered)
        self.assertIn("Charge controller PCB: 45.0C", rendered)
        self.assertLess(rendered.index("Battery cells:"), rendered.index("Battery terminal:"))

    def test_terminal_display_renders_load_totals(self) -> None:
        rendered = render_snapshot(
            Supervisor(classic=None, ambient=None).read_snapshot(),
            load_totals=LoadTotals(
                current_a=14.2,
                power_w=742.0,
                consumed_ah=3.5,
                consumed_percent=1.75,
            ),
        )

        self.assertIn("Load", rendered)
        self.assertIn("Now:                   14.2A  742W", rendered)
        self.assertIn("Cumulative Today:      3.5Ah 1.8% of bank", rendered)

    def test_terminal_display_prefers_load_summary(self) -> None:
        rendered = render_snapshot(
            Supervisor(classic=None, ambient=None).read_snapshot(),
            load_totals=LoadTotals(
                current_a=14.2,
                power_w=742.0,
                consumed_ah=3.5,
                consumed_percent=1.75,
            ),
            load_summary=LoadSummary(
                current_a=5.1,
                power_w=272,
                average_today_text="4.3A  228W",
                today_text="104.0Ah 52.0% of bank",
                remaining_text="21.2h",
            ),
        )

        self.assertIn("Now:                   5.1A  272W", rendered)
        self.assertIn("3hr Rolling Avg:       4.3A  228W", rendered)
        self.assertIn("Cumulative Today:      104.0Ah 52.0% of bank", rendered)
        self.assertIn("Estimated Autonomy:    21.2h", rendered)
        self.assertNotIn("14.2A", rendered)

    def test_terminal_display_renders_battery_can_reading(self) -> None:
        snapshot = Supervisor(
            classic=None,
            ambient=None,
            battery=FakeBatteryCanClient(),
        ).read_snapshot()

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
        self.assertIn("3.274-3.279V (5mV delta)", rendered)
        self.assertLess(battery_group.index("Flow:"), battery_group.index("Cells:"))
        self.assertLess(battery_group.index("Cells:"), battery_group.index("Protection/Alarms:"))
        self.assertLess(battery_group.index("Protection/Alarms:"), battery_group.index("Enable:"))
        self.assertLess(battery_group.index("Enable:"), battery_group.index("Limits:"))
        self.assertIn("Battery cells:", rendered)
        self.assertIn("9.9-10.9C", rendered)
        self.assertNotIn("BMS:", rendered)

    def test_terminal_display_renders_pack_charge_state(self) -> None:
        rendered = render_snapshot(Supervisor(classic=None, ambient=None, battery=FakeBatteryCanClient()).read_snapshot())

        self.assertIn("Flow:                  52.41V  0.0A  0W  idle", rendered)
        self.assertNotIn("Pack:", rendered)
        self.assertNotIn("Flow:                  52.41V  0.0A  11.3C", rendered)

    def test_terminal_display_renders_classic_hypervoc_protection(self) -> None:
        snapshot = Supervisor(
            classic=FakeClassicLiveClient(
                charge_stage_code=10,
                charge_stage="HyperVoc",
                state_code=0,
                state="Resting",
                active_flags=["HyperVoc"],
                last_voc_v=201.0,
                highest_input_voltage_v=218.0,
            ),
            ambient=None,
        ).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertIn("Charge Status:         Stage: HyperVoc  State: Resting", rendered)
        self.assertIn("PV input:              HyperVOC protection  Last Voc 201.0V  High 218.0V", rendered)

    def test_terminal_display_renders_refresh_age(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()

        rendered = render_snapshot(snapshot, now=snapshot.captured_at + timedelta(seconds=5))

        self.assertIn("Refreshed: 05 seconds ago", rendered)
        self.assertNotIn("Local time:", rendered)

    def test_format_refresh_age_uses_human_singular_and_zero(self) -> None:
        captured_at = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(format_refresh_age(captured_at, captured_at), "00 seconds ago")
        self.assertEqual(format_refresh_age(captured_at, captured_at + timedelta(seconds=1)), "01 seconds ago")

    def test_terminal_display_renders_battery_can_dfu_mode(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()
        snapshot = snapshot.__class__(
            captured_at=snapshot.captured_at,
            classic=snapshot.classic,
            classic_settings=snapshot.classic_settings,
            battery=None,
            battery_can_health=CanBusHealth(
                interface="can0",
                socketcan_present=False,
                dfu_devices=(
                    UsbDevice(
                        path=Path("/sys/bus/usb/devices/1-1.3"),
                        vendor_id="0483",
                        product_id="df11",
                        product="DFU in FS Mode",
                        serial="208634B94B45",
                    ),
                ),
            ),
            ambient=snapshot.ambient,
            errors=["CAN adapter is in DFU/bootloader mode: DFU in FS Mode serial=208634B94B45"],
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("Status:  ERROR", rendered)
        self.assertIn("CAN adapter: DFU/bootloader mode", rendered)
        self.assertIn("DFU in FS Mode serial 208634B94B45", rendered)
        self.assertIn("replug USB-CAN adapter", rendered)

    def test_terminal_display_enumerates_battery_protections_and_alarms(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()
        snapshot = snapshot.__class__(
            captured_at=snapshot.captured_at,
            classic=None,
            classic_settings=None,
            battery_can_health=None,
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

    def test_supervisor_reports_charge_controller_settings_above_bms_limits(self) -> None:
        snapshot = Supervisor(
            classic=FakeClassicSettingsClient(),
            battery=FakeLimitedBatteryCanClient(),
        ).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertFalse(snapshot.ok)
        self.assertEqual(snapshot.errors, [])
        self.assertIn("Charge controller 0 CCL exceeds battery CCL: 80.0A > 40.0A", snapshot.status_conditions)
        self.assertIn(
            "Charge controller 0 CVS exceeds battery CVL: Absorb 56.0V, Float 55.9V, "
            "Equalize 56.0V, Max temp-comp 56.0V > 55.8V",
            snapshot.status_conditions,
        )
        self.assertIn("Status:  ERROR", rendered)
        self.assertIn("Status Conditions", rendered)
        self.assertIn("Charge controller 0 CCL exceeds battery CCL", rendered)

    def test_supervisor_debounces_high_cell_and_delta_conditions(self) -> None:
        supervisor = Supervisor(
            classic=None,
            battery=FakeCellSequenceBatteryCanClient(
                [
                    (3.470, 3.555),
                    (3.470, 3.555),
                ]
            ),
        )

        first = supervisor.read_snapshot()
        second = supervisor.read_snapshot()

        self.assertTrue(first.ok)
        self.assertEqual(first.status_conditions, [])
        self.assertFalse(second.ok)
        self.assertIn("Battery cell high: max cell 3.555V >= 3.550V", second.status_conditions)
        self.assertIn(
            "Battery cell delta high: 85mV >= 75mV while max cell 3.555V >= 3.450V",
            second.status_conditions,
        )

    def test_supervisor_reports_cell_overvoltage_immediately(self) -> None:
        snapshot = Supervisor(
            classic=None,
            battery=FakeCellSequenceBatteryCanClient([(3.520, 3.610)]),
        ).read_snapshot()

        self.assertFalse(snapshot.ok)
        self.assertIn("Battery cell overvoltage risk: max cell 3.610V >= 3.600V", snapshot.status_conditions)

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
            battery_can_health=None,
            ambient=None,
            errors=[],
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("Charge Controller 0", rendered)
        self.assertNotIn("MidNite Classic", rendered)
        self.assertLess(rendered.index("Battery Bank"), rendered.index("Charge Controller 0"))
        self.assertIn("PV:                    91.2V  4.5A  Voc 101.0V", rendered)
        self.assertIn("Output:                54.8V  7.1A  389W", rendered)
        self.assertLess(rendered.index("  PV:"), rendered.index("  Output:"))
        self.assertIn("  Charge Status:         Stage: Float  State: MPPT or regulating voltage", rendered)
        self.assertIn("  Production Today:      5.8kWh  106Ah", rendered)
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
                absorb_voltage_v=55.6,
                float_voltage_v=55.0,
                equalize_voltage_v=55.6,
                sliding_current_limit_a=0,
                absorb_time_s=1950,
                max_temp_comp_voltage_v=55.6,
                min_temp_comp_voltage_v=52.0,
                temp_comp_mv_per_c_cell=0.0,
                mppt_mode_raw=0,
                aux_function_word=0,
            ),
            battery=None,
            battery_can_health=None,
            ambient=None,
            errors=[],
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("  Charge Settings:       Limit 80.0A  Absorb 55.6V for 1950s  Float 55.0V  EQ 55.6V", rendered)
        self.assertNotIn("Charge Controller 0 Settings", rendered)
        self.assertLess(rendered.index("Charge Controller 0"), rendered.index("  Charge Settings:"))
        self.assertLess(rendered.index("  Charge Settings:"), rendered.index("Temperatures"))

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
            battery_can_health=None,
            ambient=None,
            errors=[],
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("  Charge Status:         Stage: Resting", rendered)
        self.assertNotIn("State: Resting", rendered)

    def test_terminal_display_highlights_changed_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Sensor 0 ambient temp:  21.5C",
            current="Sensor 0 ambient temp:  23.5C",
        )

        self.assertIn(f"{CHANGED_DIGIT_START}23.5C{CHANGED_DIGIT_END}", highlighted)
        self.assertIn("21.5", highlight_changed_digits(None, "21.5"))

    def test_terminal_display_highlights_cell_voltage_range_as_one_token(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Cells:   3.286-3.289V",
            current="Cells:   3.287-3.290V",
        )

        self.assertIn(f"{CHANGED_DIGIT_START}3.287-3.290V{CHANGED_DIGIT_END}", highlighted)

    def test_terminal_display_does_not_highlight_refresh_age(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Refreshed: 1 second ago",
            current="Refreshed: 2 seconds ago",
        )

        self.assertNotIn(CHANGED_DIGIT_START, highlighted)
        self.assertNotIn(UP_ARROW, highlighted)

    def test_terminal_display_adds_direction_arrows_to_changed_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Output:  54.2V    3.6A    196W\nRefreshed: 1 second ago",
            current="Output:  54.1V    3.8A    190W\nRefreshed: 2 seconds ago",
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
            previous="Output:  54.2V    3.6A",
            current="Output:  54.2V    3.8A",
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

    def test_ds18b20_rejects_implausibly_high_temperature_as_disconnected(self) -> None:
        device_dir = REPO_ROOT / ".tmp-test-ds18b20-high" / "28-000001"
        device_dir.mkdir(parents=True, exist_ok=True)
        try:
            (device_dir / "w1_slave").write_text(
                "aa bb cc dd ee ff gg hh ii : crc=11 YES\n"
                "aa bb cc dd ee ff gg hh ii t=81000\n",
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

    def test_load_totals_tracker_integrates_until_local_midnight(self) -> None:
        tracker = LoadTotalsTracker(battery_capacity_ah=200)
        first = datetime(2026, 5, 28, 23, 59, 0, tzinfo=timezone.utc)
        second = first + timedelta(minutes=30)

        tracker.update(first, FakeBatteryCanClient().read(), FakeClassicLiveClient().read()[0])
        load = tracker.update(second, FakeBatteryCanClient().read(), FakeClassicLiveClient().read()[0])

        self.assertIsNotNone(load)
        self.assertAlmostEqual(load.current_a, 11.6)
        self.assertAlmostEqual(load.power_w, 625.0)
        self.assertAlmostEqual(load.consumed_ah, 5.8)
        self.assertAlmostEqual(load.consumed_percent, 2.9)


if __name__ == "__main__":
    unittest.main()
