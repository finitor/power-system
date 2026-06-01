from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import CanFrame, PylonCanSnapshot, PylonStatus, decode_pylon_snapshot
from offgrid_power.classic import ClassicTelemetry
from offgrid_power.supervisor import Supervisor, SupervisorSnapshot
from offgrid_power.web_display import (
    HouseholdLoadSummary,
    HouseholdLoadTracker,
    MIDNIGHT_SOC_UNAVAILABLE,
    SnapshotCache,
    estimate_household_average_today_text,
    estimate_household_load_current_a,
    estimate_household_remaining_text,
    estimate_household_today_text,
    household_today_text,
    is_kindle_user_agent,
    render_kindle_snapshot,
    route_display_request,
)


class WebDisplayTest(unittest.TestCase):
    def test_detects_kindle_user_agent(self) -> None:
        self.assertTrue(is_kindle_user_agent("Mozilla/5.0 (X11; U; Linux armv7l) AppleWebKit Kindle/3.0"))
        self.assertTrue(is_kindle_user_agent("Mozilla/5.0 Silk/3.13 like Chrome"))
        self.assertFalse(is_kindle_user_agent("curl/8.0"))

    def test_renders_primitive_kindle_html(self) -> None:
        snapshot = SupervisorSnapshot(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic=None,
            classic_settings=None,
            battery=decode_pylon_snapshot(
                [
                    CanFrame(0x351, bytes.fromhex("4802D007D007C001")),
                    CanFrame(0x355, bytes.fromhex("6100640000000000")),
                    CanFrame(0x356, bytes.fromhex("51151A00A7000000")),
                    CanFrame(0x359, bytes.fromhex("0000000002504E00")),
                    CanFrame(0x35C, bytes.fromhex("C000000000000000")),
                    CanFrame(0x373, bytes.fromhex("4C0D5A0D21012201")),
                ]
            ),
            ambient=None,
            errors=[],
        )

        html = render_kindle_snapshot(
            snapshot,
            household_load=HouseholdLoadSummary(
                current_a=5.1,
                power_w=272,
                average_today_text="3.2A  169W",
                today_text="5.8kWh 106Ah",
                remaining_text="18.7h",
            ),
        )

        self.assertIn('<meta http-equiv="refresh" content="60">', html)
        self.assertNotIn("<h1>", html)
        self.assertIn('class="summary"><span>SOC: 97%  Status: OK</span><span class="updated">Updated:', html)
        self.assertIn("<h2>Load</h2>", html)
        self.assertIn("<td>Now</td><td>5.1A  272W</td>", html)
        self.assertIn("<td>Average Today</td><td>3.2A  169W</td>", html)
        self.assertIn("<td>Cumulative Today</td><td>5.8kWh 106Ah</td>", html)
        self.assertIn("<td>Estimated Autonomy</td><td>18.7h</td>", html)
        self.assertLess(html.index("<h2>Load</h2>"), html.index("<h2>Battery Bank</h2>"))
        self.assertLess(html.index("<h2>Battery Bank</h2>"), html.index("<h2>Charge Controller 0</h2>"))
        self.assertIn("<td>Pack</td><td>54.57V  2.6A  charging</td>", html)
        self.assertIn("<td>Enable</td><td>charge yes  discharge yes</td>", html)
        self.assertIn("<td>Cells</td><td>3.404-3.418V (14mV delta)  15.9-16.9C</td>", html)
        self.assertNotIn("<td>SOH</td>", html)
        self.assertNotIn("<td>Limits</td>", html)
        self.assertIn("<td>Protection/Alarms</td><td>none</td>", html)
        self.assertNotIn("<td>Alarms</td>", html)
        self.assertIn("<h2>Temperatures</h2>", html)
        self.assertIn("<td>Battery</td><td>16.7 C</td>", html)
        self.assertNotIn("Classic batt", html)
        self.assertNotIn("<script", html)

    def test_renders_charge_controller_zero_rows_in_kindle_html(self) -> None:
        captured_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        snapshot = SupervisorSnapshot(
            captured_at=captured_at,
            classic=ClassicTelemetry(
                captured_at=captured_at,
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
                active_flags=[],
                battery_temp_c=17.0,
                fet_temp_c=31.0,
                pcb_temp_c=29.0,
            ),
            classic_settings=None,
            battery=None,
            ambient=None,
            errors=[],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("<h2>Charge Controller 0</h2>", html)
        self.assertIn("<td>Battery</td><td>54.8V  7.1A  389W</td>", html)
        self.assertLess(html.index("<td>PV</td>"), html.index("<td>Battery</td><td>54.8"))
        self.assertLess(html.index("<td>Battery</td><td>54.8"), html.index("<td>Stage</td>"))
        self.assertLess(html.index("<td>Stage</td>"), html.index("<td>Today Cumulative</td>"))
        self.assertIn("<td>Stage</td><td>Float  State: MPPT or regulating voltage</td>", html)
        self.assertIn("<td>Today Cumulative</td><td>5.8kWh  106Ah</td>", html)
        self.assertNotIn("<td>Temps</td>", html)

    def test_renders_redundant_charge_controller_state_only_once(self) -> None:
        captured_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        snapshot = SupervisorSnapshot(
            captured_at=captured_at,
            classic=ClassicTelemetry(
                captured_at=captured_at,
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

        html = render_kindle_snapshot(snapshot)

        self.assertIn("<td>Stage</td><td>Resting</td>", html)
        self.assertNotIn("Resting / Resting", html)

    def test_renders_bms_protections_and_alarms(self) -> None:
        snapshot = SupervisorSnapshot(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic=None,
            classic_settings=None,
            battery=PylonCanSnapshot(
                status=PylonStatus(
                    module_count=2,
                    protection_flags=("high cell voltage",),
                    alarm_flags=("charge over current",),
                    manufacturer_marker="PN",
                )
            ),
            ambient=None,
            errors=[],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("<td>Protection/Alarms</td><td>high cell voltage, charge over current</td>", html)

    def test_escapes_error_text(self) -> None:
        snapshot = SupervisorSnapshot(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic=None,
            classic_settings=None,
            battery=None,
            ambient=None,
            errors=["bad <device>"],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("bad &lt;device&gt;", html)

    def test_routes_kindle_path(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()

        response = route_display_request(snapshot, "/", "Kindle/3.0")

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertIn(b"Off-Grid Power", response.body)

    def test_snapshot_cache_returns_latest_snapshot(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()
        cache = SnapshotCache()

        with self.assertRaisesRegex(RuntimeError, "no supervisor snapshot"):
            cache.get()

        cache.set(snapshot)

        self.assertIs(cache.get(), snapshot)
        self.assertIsNone(cache.get_household_load())

    def test_estimates_household_load_from_classic_and_battery_current(self) -> None:
        class ClassicTelemetry:
            battery_current_a = 2.8

        snapshot = SupervisorSnapshot(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic=ClassicTelemetry(),
            classic_settings=None,
            battery=decode_pylon_snapshot(
                [
                    CanFrame(0x356, bytes.fromhex("0C15F4FFA7000000")),
                ]
            ),
            ambient=None,
            errors=[],
        )

        self.assertAlmostEqual(estimate_household_load_current_a(snapshot), 4.0)

    def test_household_today_text_includes_amp_hours_and_bank_percent(self) -> None:
        self.assertEqual(household_today_text(38.6, 19.3), "38.6Ah 19.3% of bank")

    def test_household_today_uses_classic_production_and_midnight_soc(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_household_today_text(snapshot, 200, 90), "104.0Ah 52.0% of bank")

    def test_household_remaining_extrapolates_usage_since_midnight(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_household_remaining_text(snapshot, 200, 90), "14.2h")

    def test_household_average_today_uses_cumulative_usage_since_midnight(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_household_average_today_text(snapshot, 200, 90), "13.0A  690W")

    def test_household_today_reports_unavailable_without_midnight_soc(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_household_today_text(snapshot, 200, None), MIDNIGHT_SOC_UNAVAILABLE)
        self.assertIsNone(estimate_household_average_today_text(snapshot, 200, None))
        self.assertIsNone(estimate_household_remaining_text(snapshot, 200, None))

    def test_household_tracker_reads_midnight_soc_log(self) -> None:
        path = REPO_ROOT / ".tmp-test-household-baselines.csv"
        path.write_text(
            "day,captured_at,soc_percent\n"
            "2026-05-31,2026-05-31T00:00:02-04:00,90\n",
            encoding="utf-8",
        )
        try:
            snapshot = self._snapshot_with_classic_and_battery(
                captured_at=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
                classic_daily_ah=108,
                current_soc=92,
            )

            summary = HouseholdLoadTracker(str(path)).update(snapshot)

            self.assertIsNotNone(summary)
            self.assertEqual(summary.average_today_text, "8.7A  460W")
            self.assertEqual(summary.today_text, "104.0Ah 52.0% of bank")
            self.assertEqual(summary.remaining_text, "21.2h")
        finally:
            path.unlink(missing_ok=True)

    def test_household_tracker_reports_unavailable_when_no_midnight_soc_log_exists(self) -> None:
        path = REPO_ROOT / ".tmp-test-missing-household-baselines.csv"
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        summary = HouseholdLoadTracker(str(path)).update(snapshot)

        self.assertIsNotNone(summary)
        self.assertIsNone(summary.average_today_text)
        self.assertEqual(summary.today_text, MIDNIGHT_SOC_UNAVAILABLE)
        self.assertIsNone(summary.remaining_text)
        self.assertFalse(path.exists())

    def test_snapshot_cache_stores_household_load_with_snapshot(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()
        household_load = HouseholdLoadSummary(current_a=5.1, power_w=272, today_text="38.6Ah 19.3% of bank")
        cache = SnapshotCache()

        cache.set(snapshot, household_load)

        self.assertIs(cache.get_household_load(), household_load)

    def _snapshot_with_classic_and_battery(
        self,
        captured_at: datetime,
        classic_daily_ah: int,
        current_soc: int,
    ) -> SupervisorSnapshot:
        return SupervisorSnapshot(
            captured_at=captured_at,
            classic=ClassicTelemetry(
                captured_at=captured_at,
                battery_voltage_v=53.0,
                pv_voltage_v=28.0,
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
                daily_amp_hours_ah=classic_daily_ah,
                lifetime_energy_kwh=1234,
                lifetime_amp_hours_ah=5678,
                info_flags=0,
                active_flags=[],
                battery_temp_c=17.0,
                fet_temp_c=31.0,
                pcb_temp_c=29.0,
            ),
            classic_settings=None,
            battery=decode_pylon_snapshot(
                [
                    CanFrame(0x355, bytes([current_soc, 0, 100, 0, 0, 0, 0, 0])),
                    CanFrame(0x356, bytes.fromhex("B814D8FFA4000000")),
                    CanFrame(0x379, bytes.fromhex("C800000000000000")),
                ]
            ),
            ambient=None,
            errors=[],
        )


if __name__ == "__main__":
    unittest.main()
