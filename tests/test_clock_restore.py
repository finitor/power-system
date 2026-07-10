from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.classic import RegisterBlock
from offgrid_power.clock_restore import (
    MAX_FORWARD_STEP_SECONDS,
    decode_classic_clock,
    restore_system_clock,
)


def clock_block(
    year: int = 2026,
    month: int = 7,
    day: int = 10,
    hour: int = 19,
    minute: int = 25,
    second: int = 5,
    day_of_week: int = 2,
    day_of_year: int = 59,
) -> RegisterBlock:
    time_word = (day_of_week << 24) | (hour << 16) | (minute << 8) | second
    date_word = (year << 16) | (month << 8) | day
    return RegisterBlock(
        4214,
        [
            time_word & 0xFFFF,
            (time_word >> 16) & 0xFFFF,
            date_word & 0xFFFF,
            (date_word >> 16) & 0xFFFF,
            day_of_year,
        ],
    )


class DecodeClassicClockTest(unittest.TestCase):
    def test_decodes_local_time_and_ignores_stale_day_of_year(self) -> None:
        decoded = decode_classic_clock(clock_block(day_of_year=59))

        self.assertEqual(decoded.isoformat(), "2026-07-10T19:25:05-04:00")

    def test_selects_dst_fold_closest_to_persisted_clock(self) -> None:
        block = clock_block(month=11, day=1, hour=1, minute=30)

        first = decode_classic_clock(
            block,
            reference=datetime(2026, 11, 1, 5, 25, tzinfo=timezone.utc),
        )
        second = decode_classic_clock(
            block,
            reference=datetime(2026, 11, 1, 6, 25, tzinfo=timezone.utc),
        )

        self.assertEqual(first.utcoffset().total_seconds(), -4 * 3600)
        self.assertEqual(second.utcoffset().total_seconds(), -5 * 3600)

    def test_rejects_nonexistent_dst_spring_forward_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonexistent local time"):
            decode_classic_clock(clock_block(month=3, day=8, hour=2, minute=30))

    def test_rejects_implausible_year(self) -> None:
        with self.assertRaisesRegex(ValueError, "implausible Classic RTC year"):
            decode_classic_clock(clock_block(year=2003))


class RestoreSystemClockTest(unittest.TestCase):
    def test_skips_classic_when_ntp_marker_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "synchronized"
            marker.touch()

            result = restore_system_clock(
                host="192.168.0.10",
                ntp_wait_seconds=0,
                timesync_marker=marker,
                read_clock_fn=lambda: self.fail("Classic must not be read after NTP sync"),
            )

        self.assertEqual(result.action, "ntp")

    def test_advances_system_clock_from_classic(self) -> None:
        system_time = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
        # Use an explicit -04:00 Classic timestamp (21:00 UTC).
        classic_time = datetime.fromisoformat("2026-07-10T17:00:00-04:00")
        writes: list[float] = []

        result = restore_system_clock(
            host="192.168.0.10",
            ignore_ntp=True,
            classic_wait_seconds=0,
            now_fn=lambda: system_time,
            read_clock_fn=lambda: classic_time,
            set_clock_fn=writes.append,
        )

        self.assertEqual(result.action, "restored")
        self.assertEqual(result.offset_seconds, 3600)
        self.assertEqual(writes, [classic_time.timestamp()])

    def test_never_steps_system_clock_backward(self) -> None:
        system_time = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)
        classic_time = datetime.fromisoformat("2026-07-10T16:59:28-04:00")
        writes: list[float] = []

        result = restore_system_clock(
            host="192.168.0.10",
            ignore_ntp=True,
            classic_wait_seconds=0,
            now_fn=lambda: system_time,
            read_clock_fn=lambda: classic_time,
            set_clock_fn=writes.append,
        )

        self.assertEqual(result.action, "not-ahead")
        self.assertEqual(writes, [])

    def test_rejects_implausibly_large_forward_step(self) -> None:
        system_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        classic_time = datetime.fromtimestamp(
            system_time.timestamp() + MAX_FORWARD_STEP_SECONDS + 1,
            timezone.utc,
        )

        result = restore_system_clock(
            host="192.168.0.10",
            ignore_ntp=True,
            classic_wait_seconds=0,
            now_fn=lambda: system_time,
            read_clock_fn=lambda: classic_time,
        )

        self.assertEqual(result.action, "invalid")


class ClockRestoreSystemdTest(unittest.TestCase):
    def test_supervisor_waits_for_clock_restore_service(self) -> None:
        supervisor_unit = (REPO_ROOT / "config/systemd/offgrid-supervisor.service").read_text()
        restore_unit = (REPO_ROOT / "config/systemd/offgrid-classic-clock-restore.service").read_text()

        self.assertIn("After=network-online.target offgrid-classic-clock-restore.service", supervisor_unit)
        self.assertIn("Wants=network-online.target offgrid-classic-clock-restore.service", supervisor_unit)
        self.assertIn("CapabilityBoundingSet=CAP_SYS_TIME", restore_unit)
        self.assertIn("ExecStart=-@PROJECT_DIR@/.venv/bin/python", restore_unit)


if __name__ == "__main__":
    unittest.main()
