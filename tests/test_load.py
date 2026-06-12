from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import time
import unittest

# The load estimates compute "hours since local midnight" and roll daily
# totals at local midnight, so expected values depend on the host timezone.
# Pin the site's zone so the suite passes identically on the Pi (Eastern),
# workstations, and UTC CI runners.
os.environ["TZ"] = "America/Toronto"
time.tzset()


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offgrid_power.canbus import CanFrame, decode_pylon_snapshot
from offgrid_power.load import (
    LoadSampleBuffer,
    LoadSummary,
    LoadTotals,
    LoadTotalsTracker,
    LoadTracker,
    LIVE_SOC_UNAVAILABLE,
    MIDNIGHT_SOC_UNAVAILABLE,
    estimate_load_average_today_text,
    estimate_load_current_a,
    estimate_load_remaining_from_average_a,
    estimate_load_remaining_text,
    estimate_load_today_text,
    load_today_text,
)
from snapshot_helpers import make_battery_snapshot, make_classic_telemetry, make_snapshot


def _load_snapshot(captured_at: datetime, classic_daily_ah: int = 108, current_soc: int = 92):
    return make_snapshot(
        captured_at=captured_at,
        classic=make_classic_telemetry(captured_at=captured_at, daily_amp_hours_ah=classic_daily_ah),
        battery=make_battery_snapshot(soc_percent=current_soc),
    )


class LoadEstimateTest(unittest.TestCase):
    def test_estimates_load_current_from_classic_and_battery_current(self) -> None:
        class FakeClassic:
            battery_current_a = 2.8

        snapshot = make_snapshot(
            classic=FakeClassic(),
            battery=decode_pylon_snapshot([CanFrame(0x356, bytes.fromhex("0C15F4FFA7000000"))]),
        )

        self.assertAlmostEqual(estimate_load_current_a(snapshot), 4.0)

    def test_load_current_unavailable_without_battery_measurements(self) -> None:
        self.assertIsNone(estimate_load_current_a(make_snapshot()))

    def test_load_today_text_includes_amp_hours_and_bank_percent(self) -> None:
        self.assertEqual(load_today_text(38.6, 19.3), "38.6Ah 19.3% of bank")

    def test_load_today_uses_classic_production_and_midnight_soc(self) -> None:
        snapshot = _load_snapshot(datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(estimate_load_today_text(snapshot, 200, 90), "104.0Ah 52.0% of bank")

    def test_load_remaining_extrapolates_load_since_midnight(self) -> None:
        snapshot = _load_snapshot(datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(estimate_load_remaining_text(snapshot, 200, 90), "14.2h")

    def test_load_average_today_uses_cumulative_load_since_midnight(self) -> None:
        snapshot = _load_snapshot(datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(estimate_load_average_today_text(snapshot, 200, 90), "13.0A  690W")

    def test_load_today_reports_unavailable_without_midnight_soc(self) -> None:
        snapshot = _load_snapshot(datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(estimate_load_today_text(snapshot, 200, None), MIDNIGHT_SOC_UNAVAILABLE)
        self.assertIsNone(estimate_load_average_today_text(snapshot, 200, None))
        self.assertIsNone(estimate_load_remaining_text(snapshot, 200, None))

    def test_load_today_attributes_missing_battery_data_not_midnight_log(self) -> None:
        # During a CAN outage the failure is live SOC, even when the midnight
        # baseline is also unavailable; blaming the midnight log is wrong.
        captured_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        no_battery = make_snapshot(
            captured_at=captured_at,
            classic=make_classic_telemetry(captured_at=captured_at),
        )

        self.assertEqual(estimate_load_today_text(no_battery, 200, None), LIVE_SOC_UNAVAILABLE)
        self.assertEqual(estimate_load_today_text(no_battery, 200, 90), LIVE_SOC_UNAVAILABLE)
        self.assertEqual(estimate_load_today_text(no_battery, None, 90), LIVE_SOC_UNAVAILABLE)

    def test_load_remaining_from_average_a_uses_amp_hours_not_voltage(self) -> None:
        snapshot = _load_snapshot(datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(estimate_load_remaining_from_average_a(snapshot, 200, 4.0), "46.0h")


class LoadTrackerTest(unittest.TestCase):
    def test_load_tracker_uses_midnight_soc_provider(self) -> None:
        snapshot = _load_snapshot(datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc))

        summary = LoadTracker(midnight_soc_provider=lambda day: 90).update(snapshot)

        self.assertIsNotNone(summary)
        self.assertIsNone(summary.average_today_text)
        self.assertEqual(summary.today_text, "104.0Ah 52.0% of bank")
        self.assertIsNone(summary.remaining_text)

    def test_load_tracker_asks_provider_once_per_day(self) -> None:
        calls: list = []

        def provider(day):
            calls.append(day)
            return None

        tracker = LoadTracker(midnight_soc_provider=provider)
        tracker.update(_load_snapshot(datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc)))
        tracker.update(_load_snapshot(datetime(2026, 5, 31, 16, 5, tzinfo=timezone.utc)))
        tracker.update(_load_snapshot(datetime(2026, 6, 1, 16, 0, tzinfo=timezone.utc)))

        self.assertEqual([day.isoformat() for day in calls], ["2026-05-31", "2026-06-01"])

    def test_load_tracker_captures_midnight_soc_live_near_midnight(self) -> None:
        # 04:00 UTC is 00:00 Eastern; the live capture wins, no provider needed.
        tracker = LoadTracker()
        tracker.update(_load_snapshot(datetime(2026, 5, 31, 4, 1, tzinfo=timezone.utc), current_soc=90))

        summary = tracker.update(_load_snapshot(datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc)))

        self.assertIsNotNone(summary)
        self.assertEqual(summary.today_text, "104.0Ah 52.0% of bank")

    def test_load_tracker_uses_three_hour_rolling_average_for_autonomy(self) -> None:
        buffer = LoadSampleBuffer()
        older_snapshot = _load_snapshot(datetime(2026, 5, 31, 13, 30, tzinfo=timezone.utc))
        recent_snapshot = _load_snapshot(datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc))
        now_snapshot = _load_snapshot(datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc))

        buffer.append(older_snapshot, LoadSummary(current_a=2.0, power_w=100))
        buffer.append(recent_snapshot, LoadSummary(current_a=4.0, power_w=200))
        summary = LoadTracker(midnight_soc_provider=lambda day: 90, sample_buffer=buffer).update(now_snapshot)

        self.assertIsNotNone(summary)
        self.assertEqual(summary.average_today_text, "3.3A  171W")
        self.assertEqual(summary.today_text, "104.0Ah 52.0% of bank")
        self.assertEqual(summary.remaining_text, "55.2h")

    def test_load_tracker_appends_samples_to_rolling_buffer(self) -> None:
        snapshot = _load_snapshot(datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc))
        buffer = LoadSampleBuffer()

        summary = LoadTracker(sample_buffer=buffer).update(snapshot)

        self.assertIsNotNone(summary)
        samples = buffer.samples(now=snapshot.captured_at)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].current_a, 4.0)
        self.assertEqual(samples[0].power_w, 212)
        self.assertEqual(samples[0].soc_percent, 92)
        self.assertAlmostEqual(samples[0].voltage_v, 53.04)

    def test_load_tracker_reports_unavailable_without_midnight_soc(self) -> None:
        snapshot = _load_snapshot(datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc))

        summary = LoadTracker().update(snapshot)

        self.assertIsNotNone(summary)
        self.assertIsNone(summary.average_today_text)
        self.assertEqual(summary.today_text, MIDNIGHT_SOC_UNAVAILABLE)
        self.assertIsNone(summary.remaining_text)


class LoadSampleBufferTest(unittest.TestCase):
    def test_load_sample_buffer_prunes_to_retention_and_reads_rolling_average(self) -> None:
        buffer = LoadSampleBuffer(retention=timedelta(hours=24))
        old_snapshot = _load_snapshot(datetime(2026, 5, 30, 11, 59, tzinfo=timezone.utc))
        recent_snapshot = _load_snapshot(datetime(2026, 5, 31, 11, 59, tzinfo=timezone.utc))
        now_snapshot = _load_snapshot(datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc))

        buffer.append(old_snapshot, LoadSummary(current_a=2.0, power_w=100))
        buffer.append(recent_snapshot, LoadSummary(current_a=4.0, power_w=200))
        buffer.append(now_snapshot, LoadSummary(current_a=6.0, power_w=300))

        samples = buffer.samples(now=now_snapshot.captured_at)
        self.assertEqual([sample.current_a for sample in samples], [4.0, 6.0])
        self.assertEqual(buffer.rolling_average(now=now_snapshot.captured_at, window=timedelta(minutes=2)), (5.0, 250.0))

    def test_load_sample_buffer_seed_restores_window_from_store_samples(self) -> None:
        from offgrid_power.load import LoadSample

        buffer = LoadSampleBuffer(retention=timedelta(hours=24))
        now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        buffer.seed(
            [
                LoadSample(captured_at=now - timedelta(minutes=1), current_a=4.0, power_w=200),
                LoadSample(captured_at=now - timedelta(hours=30), current_a=9.0, power_w=450),
                LoadSample(captured_at=now - timedelta(minutes=2), current_a=2.0, power_w=100),
            ]
        )
        buffer.append(_load_snapshot(now), LoadSummary(current_a=6.0, power_w=300))

        samples = buffer.samples(now=now)
        self.assertEqual([sample.current_a for sample in samples], [2.0, 4.0, 6.0])


class LoadTotalsTrackerTest(unittest.TestCase):
    def test_load_totals_tracker_integrates_until_local_midnight(self) -> None:
        battery = decode_pylon_snapshot(
            [
                CanFrame(0x355, bytes.fromhex("1E00640000000000")),
                CanFrame(0x356, bytes.fromhex("7914000071000000")),
            ]
        )
        classic = make_classic_telemetry(
            battery_voltage_v=53.6,
            battery_current_a=11.6,
            battery_power_w=625,
        )
        tracker = LoadTotalsTracker(battery_capacity_ah=200)
        first = datetime(2026, 5, 28, 23, 59, 0, tzinfo=timezone.utc)
        second = first + timedelta(minutes=30)

        tracker.update(first, battery, classic)
        load = tracker.update(second, battery, classic)

        self.assertIsNotNone(load)
        self.assertAlmostEqual(load.current_a, 11.6)
        self.assertAlmostEqual(load.power_w, 625.0)
        self.assertAlmostEqual(load.consumed_ah, 5.8)
        self.assertAlmostEqual(load.consumed_percent, 2.9)


if __name__ == "__main__":
    unittest.main()
