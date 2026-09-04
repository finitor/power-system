from __future__ import annotations

from datetime import date, timedelta
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

try:
    import duckdb
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("optional analysis dependency duckdb is not installed") from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cloud_telemetry_report


class CloudTelemetryReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.zone = ZoneInfo("America/Toronto")

    def test_parse_local_date_and_explicit_offset(self) -> None:
        local = cloud_telemetry_report.parse_local_datetime("2026-08-13", self.zone)
        explicit = cloud_telemetry_report.parse_local_datetime(
            "2026-08-13T12:30:00+00:00", self.zone
        )

        self.assertEqual(local.isoformat(), "2026-08-13T00:00:00-04:00")
        self.assertEqual(explicit.isoformat(), "2026-08-13T12:30:00+00:00")

    def test_partition_dates_follow_utc_not_site_day(self) -> None:
        start = cloud_telemetry_report.parse_local_datetime("2026-08-13", self.zone)
        end = start + timedelta(days=1)

        self.assertEqual(
            cloud_telemetry_report.utc_partition_dates(start, end),
            [date(2026, 8, 13), date(2026, 8, 14)],
        )

    def test_backup_credentials_are_read_without_extracting_files(self) -> None:
        backup = self.root / "backup.tar.gz"
        body = (
            "B2_APPLICATION_KEY_ID=id\n"
            "B2_APPLICATION_KEY=secret\n"
            "B2_BUCKET=bucket\n"
            "B2_ENDPOINT_URL=https://s3.example.invalid\n"
            "B2_REGION=test-1\n"
        ).encode()
        with tarfile.open(backup, "w:gz") as archive:
            member = tarfile.TarInfo("backup/etc/offgrid-power.env")
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))

        with patch.dict(os.environ, {}, clear=True):
            config = cloud_telemetry_report.load_cloud_config(backup)

        self.assertEqual(config.key_id, "id")
        self.assertEqual(config.secret, "secret")
        self.assertEqual(config.bucket, "bucket")

    def test_render_report_from_local_cache(self) -> None:
        cache = self.root / "samples.parquet"
        rows = [
            ("soc-1", "2026-08-13T04:00:00+00:00", "battery", "soc", 60.0, None, "%"),
            ("soc-2", "2026-08-14T03:59:00+00:00", "battery", "soc", 70.0, None, "%"),
            ("volt", "2026-08-13T12:00:00+00:00", "battery", "voltage", 53.2, None, "V"),
            ("current", "2026-08-13T12:00:00+00:00", "battery", "current", 1.0, None, "A"),
            ("power-1", "2026-08-13T12:00:00+00:00", "battery", "power", 53.2, None, "W"),
            ("power-2", "2026-08-13T12:01:00+00:00", "battery", "power", 53.2, None, "W"),
            ("daily", "2026-08-14T03:59:00+00:00", "classic.0", "daily_energy", 5.0, None, "kWh"),
            ("classic-1", "2026-08-13T12:00:00+00:00", "classic.0", "battery_power", 1000.0, None, "W"),
            ("classic-2", "2026-08-13T12:01:00+00:00", "classic.0", "battery_power", 1000.0, None, "W"),
            ("stage", "2026-08-13T12:00:00+00:00", "classic.0", "charge_stage", None, "Bulk", None),
            ("load-1", "2026-08-13T12:00:00+00:00", "load", "power", 200.0, None, "W"),
            ("load-2", "2026-08-13T12:01:00+00:00", "load", "power", 200.0, None, "W"),
            ("supervisor", "2026-08-13T12:00:00+00:00", "supervisor", "ok", 1.0, None, None),
            ("can", "2026-08-13T12:00:00+00:00", "battery.can", "ok", 1.0, None, None),
            ("lan", "2026-08-13T12:00:00+00:00", "network", "lan_reachable", 1.0, None, None),
            ("inverter", "2026-08-13T12:00:00+00:00", "magnum", "inverter_on", 1.0, None, None),
            ("charger", "2026-08-13T12:00:00+00:00", "magnum", "charger_on", 0.0, None, None),
            ("fault", "2026-08-13T12:00:00+00:00", "magnum", "fault", None, "NONE", None),
        ]
        connection = duckdb.connect()
        connection.execute(
            "CREATE TABLE telemetry (record_id VARCHAR, captured_at VARCHAR, "
            "source VARCHAR, metric VARCHAR, value DOUBLE, text VARCHAR, unit VARCHAR)"
        )
        connection.executemany("INSERT INTO telemetry VALUES (?,?,?,?,?,?,?)", rows)
        connection.execute(
            f"COPY telemetry TO {cloud_telemetry_report.sql_string(cache)} "
            "(FORMAT PARQUET)"
        )
        connection.close()

        start = cloud_telemetry_report.parse_local_datetime("2026-08-13", self.zone)
        end = cloud_telemetry_report.parse_local_datetime(
            "2026-08-13T23:59:59", self.zone
        )
        report = cloud_telemetry_report.render_report(cache, start, end, self.zone)

        self.assertIn("last available SOC was **70%**", report)
        self.assertIn("| 2026-08-13 | complete | 5.00 kWh", report)
        self.assertIn("Magnum fault samples: **0**", report)


if __name__ == "__main__":
    unittest.main()
