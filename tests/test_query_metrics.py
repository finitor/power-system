from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import query_metrics
import sqlite_readonly


class QueryMetricsSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.database = Path(self.tempdir.name) / "metrics.sqlite"
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE samples (value REAL)")
            connection.execute("INSERT INTO samples VALUES (42)")

    def test_owner_can_open_database_read_only(self) -> None:
        connection = query_metrics.open_db(str(self.database))
        try:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT value FROM samples").fetchone()[0], 42)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("INSERT INTO samples VALUES (43)")
        finally:
            connection.close()

    def test_non_owner_is_rejected_before_sqlite_open(self) -> None:
        different_uid = os.geteuid() + 10000
        with patch.object(sqlite_readonly.os, "geteuid", return_value=different_uid):
            with patch.object(sqlite_readonly.sqlite3, "connect") as connect:
                with self.assertRaisesRegex(SystemExit, "Even a read-only WAL connection"):
                    query_metrics.open_db(str(self.database))
        connect.assert_not_called()

    def test_snapshot_creates_consistent_copy(self) -> None:
        output = Path(self.tempdir.name) / "snapshot.sqlite"
        query_metrics.cmd_snapshot(argparse.Namespace(db=str(self.database), output=str(output)))

        with sqlite3.connect(output) as connection:
            self.assertEqual(connection.execute("SELECT value FROM samples").fetchone()[0], 42)

    def test_snapshot_refuses_to_overwrite(self) -> None:
        output = Path(self.tempdir.name) / "existing.sqlite"
        output.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "Refusing to overwrite"):
            query_metrics.cmd_snapshot(argparse.Namespace(db=str(self.database), output=str(output)))
        self.assertEqual(output.read_text(encoding="utf-8"), "keep")

    def test_snapshot_rejects_destination_without_room_before_backup(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE sample (value TEXT)")
        try:
            with patch.object(
                query_metrics.shutil,
                "disk_usage",
                return_value=SimpleNamespace(free=1),
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    r"Choose an output directory on a larger filesystem.*telemetry/snapshots",
                ):
                    query_metrics._require_snapshot_capacity(
                        connection,
                        Path("/tmp/metrics-snapshot.sqlite"),
                    )
        finally:
            connection.close()

    def test_human_size_uses_binary_units(self) -> None:
        self.assertEqual(query_metrics._human_size(3 * 1024**3), "3.0 GiB")


if __name__ == "__main__":
    unittest.main()
