from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    LoadSampleBuffer,
    LoadSummary,
    LoadTracker,
    MIDNIGHT_SOC_UNAVAILABLE,
    SnapshotCache,
    estimate_load_average_today_text,
    estimate_load_current_a,
    estimate_load_remaining_from_average_a,
    estimate_load_remaining_text,
    estimate_load_today_text,
    load_today_text,
    is_kindle_user_agent,
    render_kindle_snapshot,
    render_snapshot_unavailable,
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
            battery_can_health=None,
            ambient=None,
            errors=[],
        )

        html = render_kindle_snapshot(
            snapshot,
            load_summary=LoadSummary(
                current_a=5.1,
                power_w=272,
                average_today_text="3.2A  169W",
                today_text="5.8kWh 106Ah",
                remaining_text="18.7h",
            ),
        )

        self.assertIn('<meta http-equiv="refresh" content="60">', html)
        self.assertNotIn('<meta name="viewport"', html)
        self.assertIn("-webkit-text-size-adjust:100%", html)
        self.assertIn("td{font-size:17px;line-height:1.18;", html)
        self.assertIn(".summary-table .soc-cell{font-size:36px;line-height:1;text-align:left;vertical-align:middle;width:32%;}", html)
        self.assertIn(".summary-table .meta-cell{font-size:17px;line-height:1.15;text-align:left;width:68%;}", html)
        self.assertNotIn("<h1>", html)
        self.assertIn('<table class="summary-table">', html)
        self.assertIn('<td class="soc-cell" rowspan="2">SOC 97%</td>', html)
        self.assertIn('<td class="meta-cell">Updated:', html)
        self.assertIn('<td class="meta-cell">Status: OK</td>', html)
        self.assertNotIn("SOC: 97%  Status: OK", html)
        self.assertNotIn("Refreshed:", html)
        self.assertNotIn('class="updated"', html)
        self.assertIn("<h2>Load</h2>", html)
        self.assertIn("<td>Now</td><td>5.1A  272W</td>", html)
        self.assertIn("<td>3hr Rolling Avg</td><td>3.2A  169W</td>", html)
        self.assertIn("<td>Cumulative Today</td><td>5.8kWh 106Ah</td>", html)
        self.assertIn("<td>Estimated Autonomy</td><td>18.7h</td>", html)
        self.assertLess(html.index("<h2>Load</h2>"), html.index("<h2>Battery Bank</h2>"))
        self.assertLess(html.index("<h2>Battery Bank</h2>"), html.index("<h2>Charge Controller 0</h2>"))
        self.assertIn("<td>Flow</td><td>54.57V  2.6A  142W  charging</td>", html)
        self.assertIn("<td>Enable</td><td>charge yes  discharge yes</td>", html)
        self.assertIn("<td>Cells</td><td>3.404-3.418V (14mV delta)  15.9-16.9C</td>", html)
        battery_section = html[html.index("<h2>Battery Bank</h2>") : html.index("<h2>Charge Controller 0</h2>")]
        self.assertLess(battery_section.index("<td>Flow</td>"), battery_section.index("<td>Cells</td>"))
        self.assertLess(battery_section.index("<td>Cells</td>"), battery_section.index("<td>Protection/Alarms</td>"))
        self.assertLess(battery_section.index("<td>Protection/Alarms</td>"), battery_section.index("<td>Enable</td>"))
        self.assertNotIn("<td>SOH</td>", html)
        self.assertIn("<td>Limits</td><td>charge 58.4V/200.0A  discharge 200.0A</td>", html)
        self.assertIn("<td>Protection/Alarms</td><td>none</td>", html)
        self.assertNotIn("<td>Alarms</td>", html)
        self.assertIn("<h2>Temperatures</h2>", html)
        self.assertIn("<td>Battery cells</td><td>15.9-16.9C</td>", html)
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
            battery_can_health=None,
            ambient=None,
            errors=[],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("<h2>Charge Controller 0</h2>", html)
        self.assertIn("<td>PV</td><td>91.2V  4.5A  Voc 101.0V</td>", html)
        self.assertIn("<td>Output</td><td>54.8V  7.1A  389W</td>", html)
        self.assertLess(html.index("<td>PV</td>"), html.index("<td>Output</td><td>54.8"))
        self.assertLess(html.index("<td>Output</td><td>54.8"), html.index("<td>Charge Status</td>"))
        self.assertLess(html.index("<td>Charge Status</td>"), html.index("<td>Production Today</td>"))
        self.assertIn("<td>Charge Status</td><td>Stage: Float  State: MPPT or regulating voltage</td>", html)
        self.assertIn("<td>Production Today</td><td>5.8kWh  106Ah</td>", html)
        self.assertIn("<td>Temps</td><td>batt 17.0C  FET 31.0C  PCB 29.0C</td>", html)
        self.assertIn("<td>Battery terminal</td><td>17.0C</td>", html)
        self.assertIn("<td>Charge controller FET</td><td>31.0C</td>", html)
        self.assertIn("<td>Charge controller PCB</td><td>29.0C</td>", html)

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
            battery_can_health=None,
            ambient=None,
            errors=[],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("<td>Charge Status</td><td>Stage: Resting</td>", html)
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
            battery_can_health=None,
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
            battery_can_health=None,
            ambient=None,
            errors=["bad <device>"],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("bad &lt;device&gt;", html)

    def test_renders_status_conditions(self) -> None:
        snapshot = SupervisorSnapshot(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic=None,
            classic_settings=None,
            battery=None,
            battery_can_health=None,
            ambient=None,
            errors=[],
            status_conditions=["Charge controller 0 CCL exceeds battery CCL: 80.0A > 40.0A"],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("Status: WARNING", html)
        self.assertIn("<h2>Status Conditions</h2>", html)
        self.assertIn("Charge controller 0 CCL exceeds battery CCL: 80.0A &gt; 40.0A", html)

    def test_routes_kindle_path(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()

        response = route_display_request(snapshot, "/", "Kindle/3.0")

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertIn(b"Off-Grid Power", response.body)

    def test_snapshot_unavailable_page_auto_refreshes(self) -> None:
        html = render_snapshot_unavailable(RuntimeError("CAN bus warming up"), refresh_seconds=10)

        self.assertIn('<meta http-equiv="refresh" content="10">', html)
        self.assertIn("Snapshot unavailable", html)
        self.assertIn("CAN bus warming up", html)

    def test_snapshot_cache_returns_latest_snapshot(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()
        cache = SnapshotCache()

        with self.assertRaisesRegex(RuntimeError, "no supervisor snapshot"):
            cache.get()

        cache.set(snapshot)

        self.assertIs(cache.get(), snapshot)
        self.assertIsNone(cache.get_load_summary())

    def test_estimates_load_summary_from_classic_and_battery_current(self) -> None:
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
            battery_can_health=None,
            ambient=None,
            errors=[],
        )

        self.assertAlmostEqual(estimate_load_current_a(snapshot), 4.0)

    def test_load_today_text_includes_amp_hours_and_bank_percent(self) -> None:
        self.assertEqual(load_today_text(38.6, 19.3), "38.6Ah 19.3% of bank")

    def test_load_today_uses_classic_production_and_midnight_soc(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_load_today_text(snapshot, 200, 90), "104.0Ah 52.0% of bank")

    def test_load_remaining_extrapolates_load_since_midnight(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_load_remaining_text(snapshot, 200, 90), "14.2h")

    def test_load_average_today_uses_cumulative_load_since_midnight(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_load_average_today_text(snapshot, 200, 90), "13.0A  690W")

    def test_load_today_reports_unavailable_without_midnight_soc(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_load_today_text(snapshot, 200, None), MIDNIGHT_SOC_UNAVAILABLE)
        self.assertIsNone(estimate_load_average_today_text(snapshot, 200, None))
        self.assertIsNone(estimate_load_remaining_text(snapshot, 200, None))

    def test_load_remaining_from_average_a_uses_amp_hours_not_voltage(self) -> None:
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        self.assertEqual(estimate_load_remaining_from_average_a(snapshot, 200, 4.0), "46.0h")

    def test_load_tracker_reads_midnight_soc_log(self) -> None:
        path = REPO_ROOT / ".tmp-test-load-baselines.csv"
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

            summary = LoadTracker(str(path)).update(snapshot)

            self.assertIsNotNone(summary)
            self.assertIsNone(summary.average_today_text)
            self.assertEqual(summary.today_text, "104.0Ah 52.0% of bank")
            self.assertIsNone(summary.remaining_text)
        finally:
            path.unlink(missing_ok=True)

    def test_load_tracker_uses_three_hour_rolling_average_for_autonomy(self) -> None:
        baseline_path = REPO_ROOT / ".tmp-test-load-baselines.csv"
        sample_path = REPO_ROOT / ".tmp-test-load-samples.csv"
        baseline_path.write_text(
            "day,captured_at,soc_percent\n"
            "2026-05-31,2026-05-31T00:00:02-04:00,90\n",
            encoding="utf-8",
        )
        buffer = LoadSampleBuffer(str(sample_path), prune_interval=timedelta(seconds=0))
        try:
            older_snapshot = self._snapshot_with_classic_and_battery(
                captured_at=datetime(2026, 5, 31, 13, 30, tzinfo=timezone.utc),
                classic_daily_ah=108,
                current_soc=92,
            )
            recent_snapshot = self._snapshot_with_classic_and_battery(
                captured_at=datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc),
                classic_daily_ah=108,
                current_soc=92,
            )
            now_snapshot = self._snapshot_with_classic_and_battery(
                captured_at=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
                classic_daily_ah=108,
                current_soc=92,
            )

            buffer.append(older_snapshot, LoadSummary(current_a=2.0, power_w=100))
            buffer.append(recent_snapshot, LoadSummary(current_a=4.0, power_w=200))
            summary = LoadTracker(str(baseline_path), sample_buffer=buffer).update(now_snapshot)

            self.assertIsNotNone(summary)
            self.assertEqual(summary.average_today_text, "3.3A  171W")
            self.assertEqual(summary.today_text, "104.0Ah 52.0% of bank")
            self.assertEqual(summary.remaining_text, "55.2h")
        finally:
            baseline_path.unlink(missing_ok=True)
            sample_path.unlink(missing_ok=True)

    def test_load_tracker_appends_samples_to_rolling_buffer(self) -> None:
        path = REPO_ROOT / ".tmp-test-load-samples.csv"
        try:
            snapshot = self._snapshot_with_classic_and_battery(
                captured_at=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
                classic_daily_ah=108,
                current_soc=92,
            )
            buffer = LoadSampleBuffer(str(path))

            summary = LoadTracker(sample_buffer=buffer).update(snapshot)

            self.assertIsNotNone(summary)
            rows = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(rows[0], "captured_at,current_a,power_w,soc_percent,voltage_v")
            self.assertEqual(len(rows), 2)
            self.assertIn(",4.000,212,92,53.040", rows[1])
        finally:
            path.unlink(missing_ok=True)

    def test_load_sample_buffer_prunes_to_retention_and_reads_rolling_average(self) -> None:
        path = REPO_ROOT / ".tmp-test-load-samples.csv"
        buffer = LoadSampleBuffer(
            str(path),
            retention=timedelta(hours=24),
            prune_interval=timedelta(seconds=0),
        )
        try:
            old_snapshot = self._snapshot_with_classic_and_battery(
                captured_at=datetime(2026, 5, 30, 11, 59, tzinfo=timezone.utc),
                classic_daily_ah=108,
                current_soc=92,
            )
            recent_snapshot = self._snapshot_with_classic_and_battery(
                captured_at=datetime(2026, 5, 31, 11, 59, tzinfo=timezone.utc),
                classic_daily_ah=108,
                current_soc=92,
            )
            now_snapshot = self._snapshot_with_classic_and_battery(
                captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
                classic_daily_ah=108,
                current_soc=92,
            )

            buffer.append(old_snapshot, LoadSummary(current_a=2.0, power_w=100))
            buffer.append(recent_snapshot, LoadSummary(current_a=4.0, power_w=200))
            buffer.append(now_snapshot, LoadSummary(current_a=6.0, power_w=300))

            samples = buffer.samples(now=now_snapshot.captured_at)
            self.assertEqual([sample.current_a for sample in samples], [4.0, 6.0])
            self.assertEqual(buffer.rolling_average(now=now_snapshot.captured_at, window=timedelta(minutes=2)), (5.0, 250.0))
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 3)
        finally:
            path.unlink(missing_ok=True)

    def test_load_sample_buffer_ignores_rows_with_missing_numeric_fields(self) -> None:
        path = REPO_ROOT / ".tmp-test-load-samples.csv"
        path.write_text(
            "captured_at,current_a,power_w,soc_percent,voltage_v\n"
            "2026-05-31T11:59:00+00:00,4.000,212,92\n"
            "2026-05-31T12:00:00+00:00,6.000,318,92,53.000\n"
            "2026-05-31T12:01:00+00:00,,318,92,53.000\n",
            encoding="utf-8",
        )
        buffer = LoadSampleBuffer(str(path))
        try:
            samples = buffer.samples(now=datetime(2026, 5, 31, 12, 1, tzinfo=timezone.utc))

            self.assertEqual([sample.current_a for sample in samples], [4.0, 6.0])
            self.assertEqual(samples[0].voltage_v, None)
            self.assertEqual(samples[1].voltage_v, 53.0)
        finally:
            path.unlink(missing_ok=True)

    def test_load_tracker_reports_unavailable_when_no_midnight_soc_log_exists(self) -> None:
        path = REPO_ROOT / ".tmp-test-missing-load-baselines.csv"
        snapshot = self._snapshot_with_classic_and_battery(
            captured_at=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
            classic_daily_ah=108,
            current_soc=92,
        )

        summary = LoadTracker(str(path)).update(snapshot)

        self.assertIsNotNone(summary)
        self.assertIsNone(summary.average_today_text)
        self.assertEqual(summary.today_text, MIDNIGHT_SOC_UNAVAILABLE)
        self.assertIsNone(summary.remaining_text)
        self.assertFalse(path.exists())

    def test_snapshot_cache_stores_load_summary_with_snapshot(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()
        load_summary = LoadSummary(current_a=5.1, power_w=272, today_text="38.6Ah 19.3% of bank")
        cache = SnapshotCache()

        cache.set(snapshot, load_summary)

        self.assertIs(cache.get_load_summary(), load_summary)

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
            battery_can_health=None,
            ambient=None,
            errors=[],
        )


if __name__ == "__main__":
    unittest.main()
