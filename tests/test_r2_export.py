from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import sqlite3
import sys
import unittest
from pathlib import Path

import pyarrow.parquet as pq


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))


def _read_parquet(body: bytes) -> list[dict]:
    return pq.read_table(io.BytesIO(body)).to_pylist()

from offgrid_power.metrics import (
    MetricSample,
    TelemetryEvent,
    _insert_events,
    _insert_samples,
    initialize_metrics_db,
)
from offgrid_power.r2_export import build_export_batch, mark_batch_exported


class R2ExportTest(unittest.TestCase):
    def test_batches_are_single_table_and_date_partitioned(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            initialize_metrics_db(connection)
            sample = self._insert_sample(connection)
            event = self._insert_event(connection)

            batch = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)

            self.assertIsNotNone(batch)
            assert batch is not None
            self.assertEqual(batch.row_count, 1)
            self.assertEqual(batch.records, (("sample", 1),))
            self.assertRegex(
                batch.object_key,
                r"^metrics/samples/date=2026-06-05/20\d{6}T\d{6}Z-[0-9a-f]{32}\.parquet$",
            )
            records = _read_parquet(batch.body)
            self.assertEqual(records[0]["record_type"], "sample")
            self.assertEqual(records[0]["record_id"], sample.sample_id())
            self.assertEqual(records[0]["site_id"], "cabin")
            self.assertEqual(records[0]["source"], "battery")
            self.assertEqual(records[0]["metric"], "soc")
            self.assertEqual(records[0]["value"], 91.0)
            self.assertEqual(records[0]["unit"], "%")
            self.assertEqual(dict(records[0]["tags"]), {"pack": "0"})

            # Events drain after samples, into their own partition.
            mark_batch_exported(connection, batch, "2026-06-05T12:05:00+00:00")
            event_batch = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)
            assert event_batch is not None
            self.assertEqual(event_batch.records, (("event", 1),))
            self.assertRegex(
                event_batch.object_key,
                r"^metrics/events/date=2026-06-05/20\d{6}T\d{6}Z-[0-9a-f]{32}\.parquet$",
            )
            records = _read_parquet(event_batch.body)
            self.assertEqual(records[0]["record_type"], "event")
            self.assertEqual(records[0]["record_id"], event.event_id())
            self.assertEqual(records[0]["event"], "lbco_cutout")
            self.assertEqual(json.loads(records[0]["detail"])["fault"], "LOW_BAT")

    def test_batches_split_by_capture_date_oldest_first(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            initialize_metrics_db(connection)
            self._insert_sample(connection)  # 2026-06-05
            late = MetricSample(
                captured_at=datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
                source="battery",
                metric="soc",
                value=88.0,
                unit="%",
            )
            _insert_samples(connection, [late])

            first = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)
            assert first is not None
            self.assertIn("/date=2026-06-05/", first.object_key)
            self.assertEqual(first.row_count, 1)
            mark_batch_exported(connection, first, "2026-06-06T12:05:00+00:00")

            second = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)
            assert second is not None
            self.assertIn("/date=2026-06-06/", second.object_key)
            self.assertEqual(second.row_count, 1)

    def test_mark_batch_exported_stamps_rows_and_batch(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            initialize_metrics_db(connection)
            self._insert_sample(connection)
            batch = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)
            assert batch is not None

            mark_batch_exported(connection, batch, "2026-06-05T12:05:00+00:00")

            export_batch = connection.execute(
                "SELECT object_key, record_count, status FROM export_batches WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()
            sample_status = connection.execute(
                "SELECT exported_at, export_batch_id FROM samples WHERE id = 1"
            ).fetchone()

            self.assertEqual(export_batch, (batch.object_key, 1, "uploaded"))
            self.assertEqual(sample_status, ("2026-06-05T12:05:00+00:00", batch.batch_id))

    def test_build_export_batch_skips_exported_records(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            initialize_metrics_db(connection)
            self._insert_sample(connection)
            second = self._insert_sample(connection, minute=2)
            batch = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)
            assert batch is not None
            mark_batch_exported(connection, batch, "2026-06-05T12:05:00+00:00")
            third = self._insert_sample(connection, minute=3)

            next_batch = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)

            self.assertIsNotNone(next_batch)
            assert next_batch is not None
            self.assertEqual(next_batch.records, (("sample", 3),))
            del second, third

    def test_build_export_batch_returns_none_when_drained(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            initialize_metrics_db(connection)
            self.assertIsNone(build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10))

    def _insert_sample(self, connection: sqlite3.Connection, minute: int = 1) -> MetricSample:
        sample = MetricSample(
            captured_at=datetime(2026, 6, 5, 12, minute, tzinfo=timezone.utc),
            source="battery",
            metric="soc",
            value=91.0,
            unit="%",
            tags={"pack": "0"},
        )
        _insert_samples(connection, [sample])
        return sample

    def _insert_event(self, connection: sqlite3.Connection) -> TelemetryEvent:
        event = TelemetryEvent(
            captured_at=datetime(2026, 6, 5, 12, 1, tzinfo=timezone.utc),
            source="magnum",
            event="lbco_cutout",
            detail={"fault": "LOW_BAT", "dc_volts": 47.8},
        )
        _insert_events(connection, [event])
        return event


if __name__ == "__main__":
    unittest.main()
