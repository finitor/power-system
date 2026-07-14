from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from snapshot_helpers import (
    make_battery_snapshot,
    make_classic_telemetry,
    make_epever_settings,
    make_epever_telemetry,
    make_magnum_snapshot,
    make_snapshot,
)
from offgrid_power.canbus import CanFrame, decode_pylon_snapshot
from offgrid_power.classic import ClassicChargeSettings
from offgrid_power.load import LoadSummary
from offgrid_power.metrics import (
    MetricRecorder,
    TelemetryEvent,
    initialize_metrics_db,
    merge_metric_stores,
    parse_timestamp,
    snapshot_metric_samples,
    weather_metric_samples,
)
from offgrid_power.weather import WeatherReport


def make_classic_settings(captured_at: datetime, battery_current_limit_a: float = 80.0) -> ClassicChargeSettings:
    return ClassicChargeSettings(
        captured_at=captured_at,
        battery_current_limit_a=battery_current_limit_a,
        absorb_voltage_v=55.2,
        float_voltage_v=53.6,
        equalize_voltage_v=55.2,
        sliding_current_limit_a=80,
        absorb_time_s=360,
        max_temp_comp_voltage_v=55.2,
        min_temp_comp_voltage_v=52.0,
        temp_comp_mv_per_c_cell=0.0,
        mppt_mode_raw=1,
        aux_function_word=0,
    )


def full_snapshot(captured_at: datetime | None = None, **overrides):
    captured_at = captured_at or datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    fields = {
        "captured_at": captured_at,
        "classic": make_classic_telemetry(captured_at),
        "battery": make_battery_snapshot(),
    }
    fields.update(overrides)
    return make_snapshot(**fields)


class SnapshotMetricSamplesTest(unittest.TestCase):
    def test_samples_include_numeric_and_text_values(self) -> None:
        battery = decode_pylon_snapshot(
            [
                CanFrame(0x355, bytes([92, 0, 100, 0, 0, 0, 0, 0])),
                CanFrame(0x356, bytes.fromhex("B814D8FFA4000000")),
                CanFrame(0x373, bytes.fromhex("4C0D5A0D21012201")),
                CanFrame(0x374, bytes.fromhex("3032313400000000")),
                CanFrame(0x375, bytes.fromhex("3032313000000000")),
                CanFrame(0x379, bytes.fromhex("C800000000000000")),
            ]
        )
        snapshot = full_snapshot(
            battery=battery,
            status_conditions=["Charge controller 0 CVS exceeds battery CVL: Absorb 56.0V > 55.8V"],
        )

        samples = list(
            snapshot_metric_samples(
                snapshot,
                LoadSummary(
                    current_a=4.0,
                    power_w=212,
                    remaining_text="46.0h",
                    rolling_average_a=3.5,
                    rolling_average_w=184.0,
                ),
            )
        )

        self.assertTrue(any(sample.source == "battery" and sample.metric == "soc" and sample.value == 92 for sample in samples))
        self.assertTrue(any(sample.source == "battery" and sample.metric == "cell_voltage_delta" and sample.unit == "mV" for sample in samples))
        self.assertTrue(any(sample.source == "classic.0" and sample.metric == "charge_stage" and sample.text == "Resting" for sample in samples))
        self.assertTrue(any(sample.source == "classic.0" and sample.metric == "charge_stage_vendor" and sample.text == "Resting" for sample in samples))
        self.assertTrue(any(sample.source == "load" and sample.metric == "estimated_autonomy" and sample.value == 46.0 for sample in samples))
        self.assertTrue(any(sample.source == "load" and sample.metric == "rolling_average_current" and sample.value == 3.5 for sample in samples))
        self.assertTrue(any(sample.source == "supervisor" and sample.metric == "status_condition_count" and sample.value == 1 for sample in samples))
        self.assertTrue(any(sample.source == "supervisor" and sample.metric == "status_condition" and "CVS exceeds" in (sample.text or "") for sample in samples))

    def test_samples_cover_epever_and_magnum(self) -> None:
        snapshot = full_snapshot(
            epever=make_epever_telemetry(battery_soc_percent=88),
            epever_settings=make_epever_settings(),
            magnum=make_magnum_snapshot(),
        )

        samples = list(snapshot_metric_samples(snapshot))

        self.assertTrue(any(sample.source == "epever.1" and sample.metric == "battery_voltage" and sample.value == 53.11 for sample in samples))
        self.assertTrue(any(sample.source == "epever.1" and sample.metric == "battery_soc" and sample.value == 88 for sample in samples))
        self.assertTrue(any(sample.source == "epever.1" and sample.metric == "charge_stage" and sample.text == "Resting" for sample in samples))
        self.assertTrue(any(sample.source == "epever.1.settings" and sample.metric == "boost_voltage" and sample.value == 54.7 for sample in samples))
        self.assertTrue(any(sample.source == "magnum" and sample.metric == "dc_voltage" and sample.value == 53.2 for sample in samples))
        self.assertTrue(any(sample.source == "magnum" and sample.metric == "inverter_on" and sample.value == 1.0 for sample in samples))
        self.assertTrue(any(sample.source == "magnum" and sample.metric == "status" and sample.text == "INVERT" for sample in samples))

    def test_disabled_controller_records_only_user_enabled_heartbeat(self) -> None:
        snapshot = make_snapshot(charge_controller_enabled={0: True, 1: False})

        samples = [sample for sample in snapshot_metric_samples(snapshot) if sample.source == "epever.1"]

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].metric, "user_enabled")
        self.assertEqual(samples[0].value, 0.0)


class MetricRecorderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = REPO_ROOT / ".tmp-test-metrics.sqlite"
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)
        for sidecar in self.path.parent.glob(f"{self.path.name}*.corrupt-*"):
            sidecar.unlink()

    def test_record_snapshot_writes_flat_samples_only(self) -> None:
        recorder = MetricRecorder(str(self.path), snapshot_interval_s=60)

        recorder.record_snapshot(
            full_snapshot(),
            LoadSummary(current_a=4.0, power_w=212, remaining_text="46.0h"),
        )

        with sqlite3.connect(self.path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            sample_count = connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            soc = connection.execute(
                "SELECT value FROM samples WHERE source = 'battery' AND metric = 'soc'"
            ).fetchone()[0]
            null_ids = connection.execute(
                "SELECT COUNT(*) FROM samples WHERE sample_id IS NULL"
            ).fetchone()[0]

        self.assertNotIn("supervisor_snapshots", tables)
        self.assertNotIn("device_settings_snapshots", tables)
        self.assertNotIn("weather_snapshots", tables)
        self.assertGreater(sample_count, 20)
        self.assertEqual(soc, 92.0)
        self.assertEqual(null_ids, 0)

    def test_record_snapshot_respects_cadence_and_dedup(self) -> None:
        recorder = MetricRecorder(str(self.path), snapshot_interval_s=60)
        first = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)

        recorder.record_snapshot(full_snapshot(first))
        recorder.record_snapshot(full_snapshot(first + timedelta(seconds=30)))
        recorder.record_snapshot(full_snapshot(first + timedelta(seconds=60)))
        # Same content re-recorded by a fresh recorder is an idempotent union.
        MetricRecorder(str(self.path), snapshot_interval_s=60).record_snapshot(full_snapshot(first))

        with sqlite3.connect(self.path) as connection:
            ticks = connection.execute(
                "SELECT COUNT(DISTINCT captured_at) FROM samples WHERE source = 'supervisor'"
            ).fetchone()[0]

        self.assertEqual(ticks, 2)

    def test_record_snapshot_stores_captured_at_as_utc(self) -> None:
        recorder = MetricRecorder(str(self.path), snapshot_interval_s=60)
        local_offset = timezone(timedelta(hours=-4))
        captured_at = datetime(2026, 5, 31, 0, 1, tzinfo=local_offset)

        recorder.record_snapshot(full_snapshot(captured_at))

        with sqlite3.connect(self.path) as connection:
            stored = connection.execute(
                "SELECT captured_at FROM samples WHERE source = 'supervisor' LIMIT 1"
            ).fetchone()[0]

        self.assertEqual(stored, "2026-05-31T04:01:00+00:00")

    def test_record_event_is_hash_keyed_and_idempotent(self) -> None:
        recorder = MetricRecorder(str(self.path))
        event = TelemetryEvent(
            captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            source="charger_taper",
            event="taper_decision",
            detail={"mode": "dry-run", "target_current_a": 20.0},
        )

        recorder.record_event(event)
        recorder.record_event(event)

        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT source, event, detail_json, event_id FROM events"
            ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "charger_taper")
        self.assertEqual(rows[0][1], "taper_decision")
        self.assertIn('"target_current_a":20.0', rows[0][2])
        self.assertEqual(rows[0][3], event.event_id())

    def test_recorder_recreates_corrupt_store_and_keeps_going(self) -> None:
        self.path.write_bytes(b"this is not a sqlite database at all")
        recorder = MetricRecorder(str(self.path), snapshot_interval_s=60)

        recorder.record_snapshot(full_snapshot())

        with sqlite3.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        self.assertGreater(count, 0)
        corpses = list(self.path.parent.glob(f"{self.path.name}.corrupt-*"))
        self.assertEqual(len(corpses), 1)

    def test_recorder_swallows_unwritable_path(self) -> None:
        recorder = MetricRecorder("/dev/null/nope/metrics.sqlite")
        recorder.record_snapshot(full_snapshot())
        recorder.record_event(
            TelemetryEvent(datetime.now(timezone.utc), "test", "noop")
        )
        self.assertEqual(recorder.recent_load_samples(), [])
        self.assertIsNone(recorder.midnight_soc_percent(date(2026, 5, 31)))

    def test_record_weather_writes_samples_once_per_fetch(self) -> None:
        recorder = MetricRecorder(str(self.path))
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc),
            data={
                "current": {
                    "temperature_2m": 12.4,
                    "cloud_cover": 65,
                    "shortwave_radiation": 412.0,
                },
                "daily": {
                    "sunrise": ["2026-06-06T05:39"],
                    "sunset": ["2026-06-06T21:37"],
                    "moon_phase": [0.72],
                },
                "aurora": {
                    "forecast_time": "2026-06-06T03:12:00Z",
                    "probability_percent": 18,
                },
            },
        )

        recorder.record_weather(report)
        recorder.record_weather(report)
        MetricRecorder(str(self.path)).record_weather(report)

        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT metric, value, text FROM samples WHERE source = 'weather' ORDER BY metric"
            ).fetchall()

        by_metric = {row[0]: row for row in rows}
        self.assertEqual(len(rows), len(by_metric))  # one fetch, no duplicates
        self.assertEqual(by_metric["temperature"][1], 12.4)
        self.assertEqual(by_metric["cloud_cover"][1], 65.0)
        self.assertEqual(by_metric["shortwave_radiation"][1], 412.0)
        self.assertEqual(by_metric["sunrise"][2], "2026-06-06T05:39")
        self.assertEqual(by_metric["moon_phase"][1], 0.72)
        self.assertEqual(by_metric["aurora_probability"][1], 18.0)
        self.assertEqual(by_metric["aurora_forecast_time"][2], "2026-06-06T03:12:00Z")

    def test_recent_load_samples_round_trip(self) -> None:
        recorder = MetricRecorder(str(self.path), snapshot_interval_s=60)
        first = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        recorder.record_snapshot(full_snapshot(first), LoadSummary(current_a=4.0, power_w=212))
        recorder.record_snapshot(
            full_snapshot(first + timedelta(minutes=1)),
            LoadSummary(current_a=5.0, power_w=265),
        )

        samples = recorder.recent_load_samples(
            now=first + timedelta(minutes=2),
            window=timedelta(hours=3),
        )

        self.assertEqual([sample.current_a for sample in samples], [4.0, 5.0])
        self.assertEqual([sample.power_w for sample in samples], [212, 265])
        self.assertEqual(samples[0].captured_at, first.astimezone())

    def test_midnight_soc_percent_reads_first_sample_in_window(self) -> None:
        recorder = MetricRecorder(str(self.path), snapshot_interval_s=60)
        local_midnight = datetime(2026, 5, 31, 0, 1).astimezone()
        recorder.record_snapshot(full_snapshot(local_midnight))

        self.assertEqual(recorder.midnight_soc_percent(date(2026, 5, 31)), 92)
        self.assertIsNone(recorder.midnight_soc_percent(date(2026, 6, 1)))

    def test_midnight_soc_percent_reads_legacy_local_offset_rows(self) -> None:
        recorder = MetricRecorder(str(self.path), snapshot_interval_s=60)
        legacy_local = datetime(2026, 5, 31, 0, 2).astimezone().isoformat()
        with sqlite3.connect(self.path) as connection:
            initialize_metrics_db(connection)
            connection.execute(
                """
                INSERT INTO samples (captured_at, source, metric, value, tags_json)
                VALUES (?, 'battery', 'soc', 91.0, '{}')
                """,
                (legacy_local,),
            )

        self.assertEqual(recorder.midnight_soc_percent(date(2026, 5, 31)), 91)

    def test_local_day_utc_bounds_spans_local_calendar_day(self) -> None:
        from offgrid_power.metrics import local_day_utc_bounds

        day = date(2026, 6, 15)
        start, end = local_day_utc_bounds(day)

        # Bounds are canonical UTC text, in chronological (lexical) order.
        self.assertTrue(start.endswith("+00:00"))
        self.assertTrue(end.endswith("+00:00"))
        self.assertLess(start, end)

        # They are exactly local midnight and the next local midnight.
        self.assertEqual(parse_timestamp(start).astimezone(),
                         datetime(2026, 6, 15, 0, 0).astimezone())
        self.assertEqual(parse_timestamp(end).astimezone(),
                         datetime(2026, 6, 16, 0, 0).astimezone())

    def test_query_script_day_bounds_match_canonical_helper(self) -> None:
        # The standalone query CLI inlines its own day_bounds (stdlib-only so it
        # runs anywhere with just the DB). Guard against drift from the package
        # helper that the supervisor uses.
        from offgrid_power.metrics import local_day_utc_bounds

        scripts_dir = REPO_ROOT / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            import query_metrics
        finally:
            sys.path.remove(str(scripts_dir))

        for day in (date(2026, 1, 15), date(2026, 6, 15), date(2026, 11, 1)):
            self.assertEqual(query_metrics.day_bounds(day), local_day_utc_bounds(day))

    def test_midnight_soc_percent_ignores_samples_after_window(self) -> None:
        recorder = MetricRecorder(str(self.path), snapshot_interval_s=60)
        late = datetime(2026, 5, 31, 0, 7).astimezone()
        recorder.record_snapshot(full_snapshot(late))

        self.assertIsNone(recorder.midnight_soc_percent(date(2026, 5, 31)))

    def test_writes_to_fallback_while_primary_unmounted(self) -> None:
        fallback = REPO_ROOT / ".tmp-test-metrics-fallback.sqlite"
        self.addCleanup(lambda: [Path(f"{fallback}{s}").unlink(missing_ok=True) for s in ("", "-wal", "-shm")])
        # REPO_ROOT is a real directory but not a mountpoint, so the
        # primary is treated as absent and writes must land in the fallback.
        recorder = MetricRecorder(
            str(self.path),
            snapshot_interval_s=60,
            mountpoint=str(REPO_ROOT),
            fallback_path=str(fallback),
        )
        first = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)

        recorder.record_snapshot(full_snapshot(first), LoadSummary(current_a=4.0, power_w=212))

        self.assertFalse(self.path.exists())
        with sqlite3.connect(fallback) as connection:
            count = connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        self.assertGreater(count, 0)
        # Reads come from the fallback while the primary is unmounted.
        seeded = recorder.recent_load_samples(now=first + timedelta(minutes=1), window=timedelta(hours=1))
        self.assertEqual([sample.current_a for sample in seeded], [4.0])

    def test_merges_and_removes_fallback_when_primary_returns(self) -> None:
        fallback = REPO_ROOT / ".tmp-test-metrics-fallback.sqlite"
        self.addCleanup(lambda: [Path(f"{fallback}{s}").unlink(missing_ok=True) for s in ("", "-wal", "-shm")])
        recorder = MetricRecorder(
            str(self.path),
            snapshot_interval_s=60,
            mountpoint=str(REPO_ROOT),
            fallback_path=str(fallback),
        )
        first = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        recorder.record_snapshot(full_snapshot(first))
        recorder.record_event(TelemetryEvent(first, "magnum", "inverter_off", {"fault": "NONE"}))

        recorder.mountpoint = None  # simulate the SSD remounting
        recorder.record_snapshot(full_snapshot(first + timedelta(minutes=2)))

        self.assertFalse(fallback.exists())
        with sqlite3.connect(self.path) as connection:
            ticks = connection.execute(
                "SELECT COUNT(DISTINCT captured_at) FROM samples WHERE source = 'supervisor'"
            ).fetchone()[0]
            events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(ticks, 2)  # the gap tick was merged in
        self.assertEqual(events, 1)

    def test_falls_back_when_primary_write_fails(self) -> None:
        fallback = REPO_ROOT / ".tmp-test-metrics-fallback.sqlite"
        self.addCleanup(lambda: [Path(f"{fallback}{s}").unlink(missing_ok=True) for s in ("", "-wal", "-shm")])
        recorder = MetricRecorder(
            "/dev/null/nope/metrics.sqlite",
            snapshot_interval_s=60,
            fallback_path=str(fallback),
        )

        recorder.record_snapshot(full_snapshot())

        with sqlite3.connect(fallback) as connection:
            count = connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        self.assertGreater(count, 0)

    def test_merge_metric_stores_is_idempotent_union(self) -> None:
        other_path = REPO_ROOT / ".tmp-test-metrics-other.sqlite"
        try:
            first = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
            second = first + timedelta(minutes=2)
            MetricRecorder(str(self.path)).record_snapshot(full_snapshot(first))
            other = MetricRecorder(str(other_path))
            other.record_snapshot(full_snapshot(first))  # overlap
            other.record_snapshot(full_snapshot(second))  # gap data
            other.record_event(TelemetryEvent(second, "magnum", "inverter_off", {"fault": "NONE"}))

            inserted_samples, inserted_events = merge_metric_stores(other_path, self.path)
            again = merge_metric_stores(other_path, self.path)

            with sqlite3.connect(self.path) as connection:
                ticks = connection.execute(
                    "SELECT COUNT(DISTINCT captured_at) FROM samples WHERE source = 'supervisor'"
                ).fetchone()[0]
                events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

            self.assertEqual(ticks, 2)
            self.assertEqual(events, 1)
            self.assertGreater(inserted_samples, 0)
            self.assertEqual(inserted_events, 1)
            self.assertEqual(again, (0, 0))
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{other_path}{suffix}").unlink(missing_ok=True)


class WeatherMetricSamplesTest(unittest.TestCase):
    def test_skips_missing_fields(self) -> None:
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc),
            data={"current": {"temperature_2m": 1.5}},
        )
        samples = list(weather_metric_samples(report))
        self.assertEqual([sample.metric for sample in samples], ["temperature"])
        self.assertEqual(samples[0].value, 1.5)


if __name__ == "__main__":
    unittest.main()
