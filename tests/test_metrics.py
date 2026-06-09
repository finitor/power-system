from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import CanFrame, decode_pylon_snapshot
from offgrid_power.classic import ClassicChargeSettings, ClassicTelemetry
from offgrid_power.metrics import MetricRecorder, snapshot_metric_samples
from offgrid_power.supervisor import SupervisorSnapshot
from offgrid_power.web_display import LoadSummary
from offgrid_power.weather import WeatherReport


class MetricsTest(unittest.TestCase):
    def test_snapshot_metric_samples_include_numeric_and_text_values(self) -> None:
        snapshot = self._snapshot()
        snapshot = snapshot.__class__(
            captured_at=snapshot.captured_at,
            classic=snapshot.classic,
            classic_settings=snapshot.classic_settings,
            battery=snapshot.battery,
            battery_can_health=snapshot.battery_can_health,
            ambient=snapshot.ambient,
            errors=snapshot.errors,
            status_conditions=["Charge controller 0 CCL exceeds battery CCL: 80.0A > 40.0A"],
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
        self.assertTrue(any(sample.source == "battery" and sample.metric == "min_cell_location" and sample.text == "02:14" for sample in samples))
        self.assertTrue(any(sample.source == "battery" and sample.metric == "max_cell_location" and sample.text == "02:10" for sample in samples))
        self.assertTrue(any(sample.source == "classic.0" and sample.metric == "charge_stage" and sample.text == "Resting" for sample in samples))
        self.assertTrue(any(sample.source == "load" and sample.metric == "estimated_autonomy" and sample.value == 46.0 for sample in samples))
        self.assertTrue(any(sample.source == "load" and sample.metric == "rolling_average_current" and sample.value == 3.5 for sample in samples))
        self.assertTrue(any(sample.source == "supervisor" and sample.metric == "status_condition_count" and sample.value == 1 for sample in samples))
        self.assertTrue(any(sample.source == "supervisor" and sample.metric == "status_condition" and "CCL exceeds" in (sample.text or "") for sample in samples))

    def test_metric_recorder_records_compact_snapshot_to_sqlite(self) -> None:
        path = REPO_ROOT / ".tmp-test-metrics.sqlite"
        try:
            recorder = MetricRecorder(str(path), snapshot_interval_s=60)

            recorder.record_snapshot(
                self._snapshot(),
                LoadSummary(current_a=4.0, power_w=212, remaining_text="46.0h"),
            )

            with sqlite3.connect(path) as connection:
                rows = connection.execute(
                    """
                    SELECT captured_at, ok, status, snapshot_json
                    FROM supervisor_snapshots
                    """
                ).fetchall()
                metric_rows = connection.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0]
                payload = rows[0][3]

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1], 1)
            self.assertEqual(rows[0][2], "OK")
            self.assertIn('"battery"', payload)
            self.assertIn('"load"', payload)
            self.assertEqual(metric_rows, 0)
        finally:
            path.unlink(missing_ok=True)

    def test_metric_recorder_respects_snapshot_cadence(self) -> None:
        path = REPO_ROOT / ".tmp-test-metrics.sqlite"
        try:
            recorder = MetricRecorder(str(path), snapshot_interval_s=60)
            first = self._snapshot(captured_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc))
            second = self._snapshot(captured_at=datetime(2026, 5, 31, 12, 0, 30, tzinfo=timezone.utc))
            third = self._snapshot(captured_at=datetime(2026, 5, 31, 12, 1, tzinfo=timezone.utc))

            recorder.record_snapshot(first)
            recorder.record_snapshot(second)
            recorder.record_snapshot(third)

            with sqlite3.connect(path) as connection:
                count = connection.execute("SELECT COUNT(*) FROM supervisor_snapshots").fetchone()[0]

            self.assertEqual(count, 2)
        finally:
            path.unlink(missing_ok=True)

    def test_metric_recorder_records_settings_startup_hourly_and_changed(self) -> None:
        path = REPO_ROOT / ".tmp-test-metrics.sqlite"
        try:
            recorder = MetricRecorder(str(path), snapshot_interval_s=60, settings_interval_s=3600)
            captured_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
            recorder.record_snapshot(self._snapshot(captured_at=captured_at, classic_settings=self._classic_settings(captured_at)))
            recorder.record_snapshot(
                self._snapshot(
                    captured_at=captured_at + timedelta(minutes=30),
                    classic_settings=self._classic_settings(captured_at + timedelta(minutes=30)),
                )
            )
            recorder.record_snapshot(
                self._snapshot(
                    captured_at=captured_at + timedelta(hours=1),
                    classic_settings=self._classic_settings(captured_at + timedelta(hours=1)),
                )
            )
            recorder.record_snapshot(
                self._snapshot(
                    captured_at=captured_at + timedelta(hours=1, minutes=1),
                    classic_settings=self._classic_settings(
                        captured_at + timedelta(hours=1, minutes=1),
                        battery_current_limit_a=40.0,
                    ),
                )
            )

            with sqlite3.connect(path) as connection:
                rows = connection.execute(
                    """
                    SELECT device_id, reason, settings_json
                    FROM device_settings_snapshots
                    ORDER BY captured_at
                    """
                ).fetchall()

            self.assertEqual([row[0] for row in rows], ["classic.0", "classic.0", "classic.0"])
            self.assertEqual([row[1] for row in rows], ["startup", "hourly", "changed"])
            self.assertIn('"battery_current_limit_a":40.0', rows[-1][2])
        finally:
            path.unlink(missing_ok=True)

    def test_metric_recorder_records_weather_snapshot_once_per_fetch(self) -> None:
        path = REPO_ROOT / ".tmp-test-metrics.sqlite"
        try:
            recorder = MetricRecorder(str(path), snapshot_interval_s=60)
            report = WeatherReport(
                label="Cabin",
                fetched_at=datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc),
                data={
                    "current": {
                        "temperature_2m": 12.4,
                        "apparent_temperature": 10.1,
                        "relative_humidity_2m": 77,
                        "cloud_cover": 65,
                        "precipitation": 0.4,
                        "rain": 0.4,
                        "snowfall": 0.0,
                        "wind_speed_10m": 18,
                        "wind_gusts_10m": 32,
                        "wind_direction_10m": 250,
                        "weather_code": 61,
                        "shortwave_radiation": 412.0,
                        "direct_radiation": 280.0,
                        "diffuse_radiation": 132.0,
                        "direct_normal_irradiance": 515.0,
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
            MetricRecorder(str(path), snapshot_interval_s=60).record_weather(report)

            with sqlite3.connect(path) as connection:
                rows = connection.execute(
                    """
                    SELECT captured_at, provider, location_label, temperature_c,
                           cloud_cover_percent, shortwave_radiation_w_m2,
                           direct_normal_irradiance_w_m2, sunrise, sunset,
                           moon_phase, aurora_probability_percent,
                           aurora_forecast_time, raw_json
                    FROM weather_snapshots
                    """
                ).fetchall()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "2026-06-06T14:30:00+00:00")
            self.assertEqual(rows[0][1], "open-meteo")
            self.assertEqual(rows[0][2], "Cabin")
            self.assertEqual(rows[0][3], 12.4)
            self.assertEqual(rows[0][4], 65.0)
            self.assertEqual(rows[0][5], 412.0)
            self.assertEqual(rows[0][6], 515.0)
            self.assertEqual(rows[0][7], "2026-06-06T05:39")
            self.assertEqual(rows[0][8], "2026-06-06T21:37")
            self.assertEqual(rows[0][9], 0.72)
            self.assertEqual(rows[0][10], 18.0)
            self.assertEqual(rows[0][11], "2026-06-06T03:12:00Z")
            self.assertIn('"shortwave_radiation":412.0', rows[0][12])
        finally:
            path.unlink(missing_ok=True)

    def test_export_status_views_join_ledger_to_source_rows(self) -> None:
        path = REPO_ROOT / ".tmp-test-metrics.sqlite"
        try:
            recorder = MetricRecorder(str(path), snapshot_interval_s=60, settings_interval_s=3600)
            captured_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
            recorder.record_snapshot(self._snapshot(captured_at=captured_at, classic_settings=self._classic_settings(captured_at)))

            with sqlite3.connect(path) as connection:
                snapshot_id = connection.execute("SELECT id FROM supervisor_snapshots").fetchone()[0]
                settings_id = connection.execute("SELECT id FROM device_settings_snapshots").fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO export_batches (
                        batch_id, created_at, uploaded_at, object_key, record_count, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "batch-1",
                        "2026-05-31T12:05:00+00:00",
                        "2026-05-31T12:06:00+00:00",
                        "offgrid/cabin/metrics/batch-1.ndjson.gz",
                        2,
                        "uploaded",
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO export_batch_records (batch_id, record_type, record_id)
                    VALUES (?, ?, ?)
                    """,
                    [
                        ("batch-1", "supervisor_snapshot", snapshot_id),
                        ("batch-1", "device_settings", settings_id),
                    ],
                )
                snapshot_status = connection.execute(
                    """
                    SELECT id, export_batch_id, exported_at, export_object_key
                    FROM supervisor_snapshots_export_status
                    """
                ).fetchone()
                settings_status = connection.execute(
                    """
                    SELECT id, export_batch_id, exported_at, export_object_key
                    FROM device_settings_export_status
                    """
                ).fetchone()

            self.assertEqual(snapshot_status, (snapshot_id, "batch-1", "2026-05-31T12:06:00+00:00", "offgrid/cabin/metrics/batch-1.ndjson.gz"))
            self.assertEqual(settings_status, (settings_id, "batch-1", "2026-05-31T12:06:00+00:00", "offgrid/cabin/metrics/batch-1.ndjson.gz"))
        finally:
            path.unlink(missing_ok=True)

    def _snapshot(
        self,
        captured_at: datetime | None = None,
        classic_settings: ClassicChargeSettings | None = None,
    ) -> SupervisorSnapshot:
        captured_at = captured_at or datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
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
                daily_amp_hours_ah=108,
                lifetime_energy_kwh=1234,
                lifetime_amp_hours_ah=5678,
                info_flags=0,
                active_flags=[],
                battery_temp_c=17.0,
                fet_temp_c=31.0,
                pcb_temp_c=29.0,
            ),
            classic_settings=classic_settings,
            battery=decode_pylon_snapshot(
                [
                    CanFrame(0x355, bytes([92, 0, 100, 0, 0, 0, 0, 0])),
                    CanFrame(0x356, bytes.fromhex("B814D8FFA4000000")),
                    CanFrame(0x373, bytes.fromhex("4C0D5A0D21012201")),
                    CanFrame(0x374, bytes.fromhex("3032313400000000")),
                    CanFrame(0x375, bytes.fromhex("3032313000000000")),
                    CanFrame(0x379, bytes.fromhex("C800000000000000")),
                ]
            ),
            battery_can_health=None,
            ambient=None,
            errors=[],
        )

    def _classic_settings(
        self,
        captured_at: datetime,
        battery_current_limit_a: float = 80.0,
    ) -> ClassicChargeSettings:
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


if __name__ == "__main__":
    unittest.main()
