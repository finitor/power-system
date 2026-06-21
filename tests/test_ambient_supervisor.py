from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
from offgrid_power.classic import ClassicChargeSettings
from offgrid_power.load import LoadSummary, LoadTotals
from offgrid_power.supervisor import Supervisor
from offgrid_power.terminal_display import (
    CHANGED_DIGIT_END,
    CHANGED_DIGIT_START,
    DOWN_ARROW,
    UP_ARROW,
    format_updated_time,
    highlight_changed_digits,
    render_snapshot,
)
from snapshot_helpers import make_classic_telemetry, make_snapshot


class FakeClassicClient:
    def read(self):
        raise RuntimeError("not connected in test")


class FakeClassicLiveClient:
    def read(self):
        return (
            make_classic_telemetry(
                captured_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
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
                daily_amp_hours_ah=17,
                battery_temp_c=15.3,
                fet_temp_c=47.8,
                pcb_temp_c=45.0,
            ),
            None,
        )


class FakeClassicSettingsClient:
    def read(self):
        captured_at = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
        telemetry, _ = FakeClassicLiveClient().read()
        return (
            telemetry,
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


class FakeCurrentLimitedBatteryCanClient:
    def read(self):
        return PylonCanSnapshot(
            charge_limits=PylonChargeLimits(
                charge_voltage_limit_v=58.4,
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


class SupervisorSemanticsTest(unittest.TestCase):
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

        # The classic error is still surfaced (degraded WARNING), not hidden by
        # the ambient probe being disconnected.
        self.assertEqual(snapshot.status_text, "WARNING")
        self.assertIsNone(snapshot.ambient)
        self.assertEqual(len(snapshot.errors), 1)
        self.assertIn("Classic read failed", snapshot.errors[0])

    def test_supervisor_reports_charge_controller_voltage_settings_above_bms_limit(self) -> None:
        snapshot = Supervisor(
            classic=FakeClassicSettingsClient(),
            battery=FakeLimitedBatteryCanClient(),
        ).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertFalse(snapshot.ok)
        self.assertEqual(snapshot.errors, [])
        self.assertIn(
            "Charge controller 0 CVS exceeds battery CVL: Absorb 56.0V, Float 55.9V, "
            "Equalize 56.0V, Max temp-comp 56.0V > 55.8V",
            snapshot.status_conditions,
        )
        self.assertIn("Status:  ERROR", rendered)

    def test_supervisor_does_not_warn_when_controller_current_limit_exceeds_bms_ccl(self) -> None:
        snapshot = Supervisor(
            classic=FakeClassicSettingsClient(),
            battery=FakeCurrentLimitedBatteryCanClient(),
        ).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertTrue(snapshot.ok)
        self.assertEqual(snapshot.status_text, "OK")
        self.assertEqual(snapshot.status_conditions, [])
        self.assertIn("Status:  OK", rendered)

    def test_terminal_display_renders_charge_allocation(self) -> None:
        snapshot = make_snapshot()
        rendered = render_snapshot(
            snapshot,
            allocation={
                "mode": "live",
                "reason": "unconstrained",
                "bms_ccl_a": 200.0,
                "charge_ceiling_a": None,
                "budget_a": 200.0,
                "battery_current_a": -2.0,
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
            },
        )

        self.assertIn("Charge Allocation", rendered)
        self.assertIn("Limit:                 not limiting (BMS CCL 200A)", rendered)
        self.assertIn("Budget:                200A  split equally", rendered)
        self.assertNotIn("battery -2A", rendered)
        self.assertIn("Classic:               100.0A released", rendered)
        self.assertIn("Epever:                100.0A released  *", rendered)

    def test_supervisor_does_not_warn_on_high_cell_or_delta_conditions(self) -> None:
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
        self.assertTrue(second.ok)
        self.assertEqual(second.status_text, "OK")
        self.assertEqual(second.status_conditions, [])

    def test_supervisor_does_not_error_on_cell_overvoltage(self) -> None:
        snapshot = Supervisor(
            classic=None,
            battery=FakeCellSequenceBatteryCanClient([(3.520, 3.610)]),
        ).read_snapshot()

        self.assertTrue(snapshot.ok)
        self.assertEqual(snapshot.status_text, "OK")
        self.assertEqual(snapshot.status_conditions, [])

    def test_unconfigured_adapters_are_marked_disabled(self) -> None:
        snapshot = Supervisor(classic=None, battery=FakeBatteryCanClient()).read_snapshot()

        # No client configured -> disabled (no read attempted), not offline.
        self.assertIn("magnum", snapshot.disabled_devices)
        self.assertIn("classic", snapshot.disabled_devices)
        self.assertIn("epever", snapshot.disabled_devices)
        # A configured adapter is not disabled.
        self.assertNotIn("battery", snapshot.disabled_devices)


class TerminalDisplayTest(unittest.TestCase):
    def test_renders_full_snapshot_with_all_groups(self) -> None:
        snapshot = Supervisor(
            classic=FakeClassicLiveClient(),
            ambient=FakeAmbientClient(),
            battery=FakeBatteryCanClient(),
        ).read_snapshot()

        rendered = render_snapshot(snapshot)

        for group in ("Load", "Battery Bank", "Charge Controller 0 (Classic)", "Inverter/Charger", "Temperatures"):
            self.assertIn(group, rendered)
        self.assertIn("21.5C", rendered)
        self.assertIn("44.0%", rendered)
        self.assertIn("52.41V", rendered)
        self.assertIn("SOC:  30%", rendered)
        self.assertNotIn("CC1 battery", rendered)
        self.assertNotIn("CC1 device", rendered)

    def test_renders_disconnected_ambient_probe(self) -> None:
        rendered = render_snapshot(Supervisor(classic=None, ambient=None).read_snapshot())

        self.assertIn("Sensor 0 ambient temp: disconnected", rendered)

    def test_renders_load_totals(self) -> None:
        rendered = render_snapshot(
            Supervisor(classic=None, ambient=None).read_snapshot(),
            load_totals=LoadTotals(
                current_a=14.2,
                power_w=742.0,
                consumed_ah=3.5,
                consumed_percent=1.75,
            ),
        )

        self.assertIn("14.2A", rendered)
        self.assertIn("3.5Ah 1.8% of bank", rendered)

    def test_prefers_load_summary_over_load_totals(self) -> None:
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

        self.assertIn("5.1A", rendered)
        self.assertIn("104.0Ah 52.0% of bank", rendered)
        self.assertIn("21.2h", rendered)
        self.assertNotIn("14.2A", rendered)

    def test_renders_classic_hypervoc_protection(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(
                charge_stage_code=10,
                charge_stage="HyperVoc",
                state="Resting",
                active_flags=["HyperVoc"],
                last_voc_v=201.0,
                highest_input_voltage_v=218.0,
            ),
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("HyperVOC protection", rendered)
        self.assertIn("201.0V", rendered)
        self.assertIn("218.0V", rendered)

    def test_renders_updated_timestamp(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertIn(f"Updated: {format_updated_time(snapshot.captured_at)}", rendered)
        self.assertNotIn("Refreshed:", rendered)

    def test_renders_battery_can_dfu_mode(self) -> None:
        snapshot = make_snapshot(
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
            errors=["CAN adapter is in DFU/bootloader mode: DFU in FS Mode serial=208634B94B45"],
        )

        rendered = render_snapshot(snapshot)

        # A wedged CAN adapter means the battery is offline — degraded (WARNING),
        # not a supervisor-level ERROR; the actionable detail still renders.
        self.assertIn("Status:  WARNING", rendered)
        self.assertIn("CAN adapter: DFU/bootloader mode", rendered)
        self.assertIn("DFU in FS Mode serial 208634B94B45", rendered)
        self.assertIn("replug USB-CAN adapter", rendered)

    def test_battery_protections_render_as_status_conditions(self) -> None:
        # Protections/alarms are surfaced as Status Conditions by the supervisor
        # (severity-bearing), not as a passive battery row.
        snapshot = make_snapshot(
            battery=PylonCanSnapshot(
                status=PylonStatus(
                    module_count=2,
                    protection_flags=("high cell voltage",),
                    alarm_flags=("charge over current",),
                    manufacturer_marker="PN",
                )
            ),
            status_conditions=["BMS protection: high cell voltage", "BMS alarm: charge over current"],
            status_severity="ERROR",
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("Status Conditions", rendered)
        self.assertIn("BMS protection: high cell voltage", rendered)
        self.assertIn("BMS alarm: charge over current", rendered)
        self.assertNotIn("Protection/Alarms", rendered)

    def test_battery_protection_candidates_map_to_severity(self) -> None:
        from offgrid_power.supervisor import (
            STATUS_ERROR,
            STATUS_WARNING,
            battery_protection_status_condition_candidates,
            status_condition_severity,
        )

        battery = PylonCanSnapshot(
            status=PylonStatus(
                module_count=2,
                protection_flags=("cell/module over voltage",),
                alarm_flags=("charge high current",),
                manufacturer_marker="PN",
            )
        )
        candidates = battery_protection_status_condition_candidates(battery)
        by_text = {c.text: c.severity for c in candidates}
        self.assertEqual(by_text["BMS protection: cell/module over voltage"], STATUS_ERROR)
        self.assertEqual(by_text["BMS alarm: charge high current"], STATUS_WARNING)
        # A protection drives overall severity to ERROR (-> /api/v1/health 503).
        self.assertEqual(status_condition_severity(candidates), STATUS_ERROR)

    def test_hides_redundant_charge_controller_state(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(charge_stage="Resting", state="Resting"),
        )

        rendered = render_snapshot(snapshot)

        self.assertIn("Stage: Resting", rendered)
        self.assertNotIn("State: Resting", rendered)


class HighlightChangedDigitsTest(unittest.TestCase):
    def test_highlights_changed_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Sensor 0 ambient temp:  21.5C",
            current="Sensor 0 ambient temp:  23.5C",
        )

        self.assertIn(f"{CHANGED_DIGIT_START}23.5C{CHANGED_DIGIT_END}", highlighted)
        self.assertIn("21.5", highlight_changed_digits(None, "21.5"))

    def test_highlights_cell_voltage_range_as_one_token(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Cells:   3.286-3.289V",
            current="Cells:   3.287-3.290V",
        )

        self.assertIn(f"{CHANGED_DIGIT_START}3.287-3.290V{CHANGED_DIGIT_END}", highlighted)

    def test_does_not_highlight_refresh_age(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Updated: 12:00:01 EDT",
            current="Updated: 12:00:02 EDT",
        )

        self.assertNotIn(CHANGED_DIGIT_START, highlighted)
        self.assertNotIn(UP_ARROW, highlighted)

    def test_adds_direction_arrows_to_changed_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Output:  54.2V    3.6A    196W\nUpdated: 12:00:01 EDT",
            current="Output:  54.1V    3.8A    190W\nUpdated: 12:00:02 EDT",
        )

        self.assertIn(DOWN_ARROW, highlighted)
        self.assertIn(UP_ARROW, highlighted)
        self.assertEqual(highlighted.count(UP_ARROW) + highlighted.count(DOWN_ARROW), 3)

    def test_highlights_millivolt_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Cells: 3.399-3.414V (15mV delta)",
            current="Cells: 3.399-3.414V (16mV delta)",
        )

        self.assertIn(f"{CHANGED_DIGIT_START}16mV{CHANGED_DIGIT_END}", highlighted)
        self.assertIn(UP_ARROW, highlighted)

    def test_pads_unchanged_value_arrow_slots(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Output:  54.2V    3.6A",
            current="Output:  54.2V    3.8A",
        )

        self.assertIn("54.2V ", highlighted)
        self.assertIn(UP_ARROW, highlighted)


class Ds18b20Test(unittest.TestCase):
    def test_reads_sysfs_temperature(self) -> None:
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

    def test_rejects_zero_temperature_as_disconnected(self) -> None:
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

    def test_rejects_implausibly_high_temperature_as_disconnected(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
