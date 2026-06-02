from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import CanFrame, decode_pylon_snapshot
from offgrid_power.classic import ClassicTelemetry
from offgrid_power.metrics import MetricRecorder, snapshot_metric_samples
from offgrid_power.supervisor import SupervisorSnapshot
from offgrid_power.web_display import LoadSummary


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
        self.assertTrue(any(sample.source == "classic.0" and sample.metric == "charge_stage" and sample.text == "Resting" for sample in samples))
        self.assertTrue(any(sample.source == "load" and sample.metric == "estimated_autonomy" and sample.value == 46.0 for sample in samples))
        self.assertTrue(any(sample.source == "load" and sample.metric == "rolling_average_current" and sample.value == 3.5 for sample in samples))
        self.assertTrue(any(sample.source == "supervisor" and sample.metric == "status_condition_count" and sample.value == 1 for sample in samples))
        self.assertTrue(any(sample.source == "supervisor" and sample.metric == "status_condition" and "CCL exceeds" in (sample.text or "") for sample in samples))

    def test_metric_recorder_appends_to_sqlite(self) -> None:
        path = REPO_ROOT / ".tmp-test-metrics.sqlite"
        try:
            recorder = MetricRecorder(str(path))

            recorder.record_snapshot(
                self._snapshot(),
                LoadSummary(current_a=4.0, power_w=212, remaining_text="46.0h"),
            )

            with sqlite3.connect(path) as connection:
                rows = connection.execute(
                    """
                    SELECT source, metric, value, text, unit
                    FROM metric_samples
                    WHERE source = ? AND metric IN (?, ?)
                    ORDER BY metric
                    """,
                    ("load", "current", "estimated_autonomy"),
                ).fetchall()
                index_count = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name LIKE 'metric_samples_%'"
                ).fetchone()[0]

            self.assertEqual(rows, [("load", "current", 4.0, None, "A"), ("load", "estimated_autonomy", 46.0, None, "h")])
            self.assertGreaterEqual(index_count, 2)
        finally:
            path.unlink(missing_ok=True)

    def _snapshot(self) -> SupervisorSnapshot:
        captured_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
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
            classic_settings=None,
            battery=decode_pylon_snapshot(
                [
                    CanFrame(0x355, bytes([92, 0, 100, 0, 0, 0, 0, 0])),
                    CanFrame(0x356, bytes.fromhex("B814D8FFA4000000")),
                    CanFrame(0x373, bytes.fromhex("4C0D5A0D21012201")),
                    CanFrame(0x379, bytes.fromhex("C800000000000000")),
                ]
            ),
            battery_can_health=None,
            ambient=None,
            errors=[],
        )


if __name__ == "__main__":
    unittest.main()
