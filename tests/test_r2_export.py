from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
import sqlite3
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.metrics import initialize_metrics_db
from offgrid_power.r2_export import build_export_batch, mark_batch_exported


class R2ExportTest(unittest.TestCase):
    def test_build_export_batch_uses_unexported_compact_records(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            initialize_metrics_db(connection)
            self._insert_snapshot(connection, 1)
            self._insert_settings(connection, 1)
            self._insert_weather(connection, 1)

            batch = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)

            self.assertIsNotNone(batch)
            assert batch is not None
            self.assertEqual(batch.row_count, 3)
            self.assertEqual(batch.records, (("supervisor_snapshot", 1), ("device_settings", 1), ("weather_snapshot", 1)))
            self.assertRegex(
                batch.object_key,
                r"^metrics/20\d{6}T\d{6}Z-[0-9a-f]{32}\.ndjson\.gz$",
            )
            records = [json.loads(line) for line in gzip.decompress(batch.body).decode("utf-8").splitlines()]
            self.assertEqual(records[0]["record_type"], "supervisor_snapshot")
            self.assertEqual(records[0]["record_id"], "supervisor_snapshot:1")
            self.assertEqual(records[0]["snapshot"]["battery"]["soc_percent"], 91)
            self.assertEqual(records[1]["record_type"], "device_settings")
            self.assertEqual(records[1]["record_id"], "device_settings:1")
            self.assertEqual(records[1]["device_id"], "classic.0")
            self.assertEqual(records[1]["reason"], "startup")
            self.assertEqual(records[1]["settings"]["float_voltage_v"], 53.6)
            self.assertEqual(records[2]["record_type"], "weather_snapshot")
            self.assertEqual(records[2]["record_id"], "weather_snapshot:1")
            self.assertEqual(records[2]["weather"]["current"]["cloud_cover"], 65)
            self.assertEqual(records[2]["weather"]["current"]["shortwave_radiation"], 412)

    def test_mark_batch_exported_records_ledger_rows(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            initialize_metrics_db(connection)
            self._insert_snapshot(connection, 1)
            self._insert_settings(connection, 1)
            batch = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)
            assert batch is not None

            mark_batch_exported(connection, batch, "2026-06-05T12:05:00+00:00")

            export_batch = connection.execute(
                "SELECT object_key, record_count, status FROM export_batches WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()
            ledger_rows = connection.execute(
                """
                SELECT record_type, record_id
                FROM export_batch_records
                ORDER BY record_type, record_id
                """
            ).fetchall()
            snapshot_status = connection.execute(
                """
                SELECT export_batch_id, exported_at
                FROM supervisor_snapshots_export_status
                WHERE id = 1
                """
            ).fetchone()

            self.assertEqual(export_batch, (batch.object_key, 2, "uploaded"))
            self.assertEqual(ledger_rows, [("device_settings", 1), ("supervisor_snapshot", 1)])
            self.assertEqual(snapshot_status, (batch.batch_id, "2026-06-05T12:05:00+00:00"))

    def test_build_export_batch_skips_exported_records(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            initialize_metrics_db(connection)
            self._insert_snapshot(connection, 1)
            self._insert_snapshot(connection, 2)
            connection.execute(
                """
                INSERT INTO export_batches (
                    batch_id, created_at, uploaded_at, object_key, record_count, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "batch-1",
                    "2026-06-05T12:00:00+00:00",
                    "2026-06-05T12:01:00+00:00",
                    "metrics/batch-1.ndjson.gz",
                    1,
                    "uploaded",
                ),
            )
            connection.execute(
                """
                INSERT INTO export_batch_records (batch_id, record_type, record_id)
                VALUES (?, ?, ?)
                """,
                ("batch-1", "supervisor_snapshot", 1),
            )

            batch = build_export_batch(connection, site_id="cabin", prefix="metrics", limit=10)

            self.assertIsNotNone(batch)
            assert batch is not None
            self.assertEqual(batch.records, (("supervisor_snapshot", 2),))

    def _insert_snapshot(self, connection: sqlite3.Connection, row_id: int) -> None:
        captured_at = datetime(2026, 6, 5, 12, row_id, tzinfo=timezone.utc).isoformat()
        payload = {
            "schema_version": 1,
            "site_id": "cabin",
            "captured_at": captured_at,
            "status": {"ok": True, "severity": "OK", "errors": [], "conditions": []},
            "battery": {"soc_percent": 90 + row_id},
        }
        connection.execute(
            """
            INSERT INTO supervisor_snapshots (id, captured_at, ok, status, snapshot_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row_id, captured_at, 1, "OK", json.dumps(payload, sort_keys=True)),
        )

    def _insert_settings(self, connection: sqlite3.Connection, row_id: int) -> None:
        captured_at = datetime(2026, 6, 5, 12, row_id, tzinfo=timezone.utc).isoformat()
        settings = {"float_voltage_v": 53.6, "absorb_voltage_v": 55.2}
        connection.execute(
            """
            INSERT INTO device_settings_snapshots (
                id, captured_at, device_id, settings_hash, reason, settings_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (row_id, captured_at, "classic.0", "hash-1", "startup", json.dumps(settings, sort_keys=True)),
        )

    def _insert_weather(self, connection: sqlite3.Connection, row_id: int) -> None:
        captured_at = datetime(2026, 6, 5, 12, row_id, tzinfo=timezone.utc).isoformat()
        payload = {
            "current": {
                "temperature_2m": 12.4,
                "cloud_cover": 65,
                "shortwave_radiation": 412,
                "direct_normal_irradiance": 515,
            }
        }
        connection.execute(
            """
            INSERT INTO weather_snapshots (
                id, captured_at, provider, location_label, temperature_c,
                cloud_cover_percent, shortwave_radiation_w_m2,
                direct_normal_irradiance_w_m2, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                captured_at,
                "open-meteo",
                "Cabin",
                12.4,
                65,
                412,
                515,
                json.dumps(payload, sort_keys=True),
            ),
        )


if __name__ == "__main__":
    unittest.main()
