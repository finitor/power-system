from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import PylonCanSnapshot, PylonMeasurements
from offgrid_power.readers import DeviceReading, PollingReader
from offgrid_power.supervisor import Supervisor, validated_battery_snapshot


class PollingReaderTest(unittest.TestCase):
    def test_read_now_caches_value_and_clears_error(self) -> None:
        reader = PollingReader("dev", lambda: 42, interval_s=5.0)

        reader.read_now()
        reading = reader.reading()

        self.assertEqual(reading.value, 42)
        self.assertIsNotNone(reading.captured_at)
        self.assertIsNone(reading.error)

    def test_failure_keeps_last_good_value_and_records_error(self) -> None:
        calls = iter([lambda: 42, lambda: (_ for _ in ()).throw(RuntimeError("bus timeout"))])
        reader = PollingReader("dev", lambda: next(calls)(), interval_s=5.0)

        reader.read_now()
        good_at = reader.reading().captured_at
        reader.read_now()
        reading = reader.reading()

        self.assertEqual(reading.value, 42)
        self.assertEqual(reading.captured_at, good_at)
        self.assertEqual(reading.error, "bus timeout")

    def test_none_return_counts_as_failure(self) -> None:
        reader = PollingReader("dev", lambda: None, interval_s=5.0)

        reader.read_now()
        reading = reader.reading()

        self.assertIsNone(reading.value)
        self.assertEqual(reading.error, "no reading")

    def test_staleness_thresholds(self) -> None:
        now = datetime.now(timezone.utc)
        fresh = DeviceReading("dev", 1, now - timedelta(seconds=5), None, stale_after_s=20)
        stale = DeviceReading("dev", 1, now - timedelta(seconds=45), None, stale_after_s=20)
        never = DeviceReading("dev", None, None, "down", stale_after_s=20)

        self.assertFalse(fresh.is_stale(now))
        self.assertTrue(stale.is_stale(now))
        self.assertFalse(never.is_stale(now))
        self.assertAlmostEqual(fresh.age_seconds(now), 5.0)
        self.assertIsNone(never.age_seconds(now))

    def test_expiry_is_separate_from_staleness(self) -> None:
        now = datetime.now(timezone.utc)
        # Stale (>20s) but within the 300s expiry: warn, keep showing.
        grace = DeviceReading("dev", 1, now - timedelta(seconds=120), None, 20, expire_after_s=300)
        expired = DeviceReading("dev", 1, now - timedelta(seconds=400), None, 20, expire_after_s=300)
        no_expiry = DeviceReading("dev", 1, now - timedelta(seconds=9999), None, 20)

        self.assertTrue(grace.is_stale(now))
        self.assertFalse(grace.is_expired(now))
        self.assertTrue(expired.is_expired(now))
        self.assertFalse(no_expiry.is_expired(now))  # None expiry never drops

    def test_submit_runs_command_on_actor_thread(self) -> None:
        reader = PollingReader("dev", lambda: 1, interval_s=60.0)
        reader.start()
        try:
            command_thread = reader.submit(lambda: threading.current_thread().name)
        finally:
            reader.stop()

        self.assertEqual(command_thread, "reader-dev")

    def test_submit_propagates_command_exception(self) -> None:
        reader = PollingReader("dev", lambda: 1, interval_s=60.0)
        reader.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "write rejected"):
                reader.submit(lambda: (_ for _ in ()).throw(RuntimeError("write rejected")))
        finally:
            reader.stop()

    def test_submit_executes_inline_when_not_started(self) -> None:
        reader = PollingReader("dev", lambda: 1, interval_s=60.0)

        self.assertEqual(reader.submit(lambda: "inline"), "inline")

    def test_request_refresh_polls_out_of_cycle_without_blocking(self) -> None:
        reads = []

        def slow_read() -> int:
            time.sleep(0.05)
            reads.append(1)
            return len(reads)

        # Long interval: without request_refresh only the initial poll happens.
        reader = PollingReader("dev", slow_read, interval_s=60.0)
        reader.start()
        try:
            deadline = time.monotonic() + 2.0
            while reader.reading().captured_at is None and time.monotonic() < deadline:
                time.sleep(0.01)
            count_after_first = len(reads)

            started = time.monotonic()
            reader.request_refresh()  # fire-and-forget: must return immediately
            self.assertLess(time.monotonic() - started, 0.02)

            deadline = time.monotonic() + 2.0
            while len(reads) <= count_after_first and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            reader.stop()

        self.assertEqual(len(reads), count_after_first + 1)

    def test_request_refresh_reads_inline_when_not_started(self) -> None:
        reads = []
        reader = PollingReader("dev", lambda: reads.append(1) or len(reads), interval_s=60.0)

        reader.request_refresh()

        self.assertEqual(len(reads), 1)

    def test_reads_and_commands_share_one_thread(self) -> None:
        seen_threads: set[str] = set()

        def read_fn() -> int:
            seen_threads.add(threading.current_thread().name)
            return 1

        reader = PollingReader("dev", read_fn, interval_s=0.01)
        reader.start()
        try:
            seen_threads.add(reader.submit(lambda: threading.current_thread().name))
            time.sleep(0.05)
        finally:
            reader.stop()

        self.assertEqual(seen_threads, {"reader-dev"})
        self.assertIsNotNone(reader.reading().captured_at)

    def test_error_rate_hidden_until_window_warms_up(self) -> None:
        reader = PollingReader("dev", lambda: 1, interval_s=5.0)
        now = time.monotonic()
        reader._poll_history.append((now - 15.0, True))
        reader._poll_history.append((now - 5.0, False))

        self.assertIsNone(reader.error_rate_pct(window_s=30.0))

    def test_error_rate_counts_failures_after_warmup(self) -> None:
        reader = PollingReader("dev", lambda: 1, interval_s=5.0)
        now = time.monotonic()
        reader._poll_history.append((now - 40.0, True))
        reader._poll_history.append((now - 20.0, False))
        reader._poll_history.append((now - 10.0, True))

        self.assertAlmostEqual(reader.error_rate_pct(window_s=30.0), 50.0)


class FakeReader:
    def __init__(
        self, name: str, value, captured_at, error=None, stale_after_s: float = 20.0, expire_after_s=None
    ) -> None:
        self._reading = DeviceReading(name, value, captured_at, error, stale_after_s, expire_after_s)

    def reading(self) -> DeviceReading:
        return self._reading

    def error_rate_pct(self, window_s: float = 300.0) -> float | None:
        return None

    def stop(self, timeout_s: float = 2.0) -> None:
        pass


class SupervisorReaderModeTest(unittest.TestCase):
    def _supervisor_with_readers(self, readers: dict) -> Supervisor:
        supervisor = Supervisor(classic=None)
        supervisor._readers = readers
        return supervisor

    def test_composes_from_reader_caches(self) -> None:
        from snapshot_helpers import make_classic_telemetry

        now = datetime.now(timezone.utc)

        class FakeMagnumValue:
            pass

        magnum_value = FakeMagnumValue()
        classic_value = make_classic_telemetry()
        supervisor = self._supervisor_with_readers(
            {
                "classic": FakeReader("classic", (classic_value, "settings"), now),
                "magnum": FakeReader("magnum", magnum_value, now),
            }
        )

        snapshot = supervisor.read_snapshot()

        self.assertIs(snapshot.classic, classic_value)
        self.assertEqual(snapshot.classic_settings, "settings")
        self.assertIs(snapshot.magnum, magnum_value)
        self.assertEqual(snapshot.errors, [])
        self.assertEqual(snapshot.status_conditions, [])

    def test_classic_arc_fault_surfaces_as_error_condition(self) -> None:
        # An arc/ground fault latches the Classic off; the supervisor must surface
        # it as a severity-bearing Warnings-and-Faults condition (-> alertable),
        # not leave it buried in the passive active_flags list.
        from snapshot_helpers import make_classic_telemetry

        classic = make_classic_telemetry(active_flags=["Arc fault"])
        supervisor = self._supervisor_with_readers(
            {"classic": FakeReader("classic", (classic, None), datetime.now(timezone.utc))}
        )

        snapshot = supervisor.read_snapshot()

        self.assertTrue(
            any("arc fault" in c.lower() for c in snapshot.status_conditions),
            snapshot.status_conditions,
        )
        self.assertEqual(snapshot.status_text, "ERROR")

    def test_reader_error_collapses_with_staleness_into_one_aged_message(self) -> None:
        # A failing read that is also stale must yield ONE message carrying the
        # age of the last good telemetry -- not a separate "read failed" error
        # plus a "telemetry stale" condition that say the same thing twice.
        stale = datetime.now(timezone.utc) - timedelta(seconds=45)
        supervisor = self._supervisor_with_readers(
            {"magnum": FakeReader("magnum", "last-good", stale, stale_after_s=20.0, error="serial port vanished")}
        )

        snapshot = supervisor.read_snapshot()

        self.assertEqual(snapshot.magnum, "last-good")
        self.assertEqual(len(snapshot.errors), 1)
        self.assertIn("Magnum read failed (last good read", snapshot.errors[0])
        self.assertIn("s ago)", snapshot.errors[0])
        self.assertEqual(snapshot.status_conditions, [])

    def test_reader_error_without_prior_value_keeps_exception_detail(self) -> None:
        # Never read successfully -> no age to report, so keep the raw exception.
        supervisor = self._supervisor_with_readers(
            {"magnum": FakeReader("magnum", None, None, error="serial port vanished")}
        )

        snapshot = supervisor.read_snapshot()

        self.assertEqual(snapshot.errors, ["Magnum read failed: serial port vanished"])

    def test_stale_reading_raises_warning_condition(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        supervisor = self._supervisor_with_readers(
            {"magnum": FakeReader("magnum", "old-value", old, stale_after_s=20.0)}
        )

        snapshot = supervisor.read_snapshot()

        self.assertEqual(snapshot.magnum, "old-value")
        self.assertEqual(snapshot.status_text, "WARNING")
        self.assertTrue(
            any("Magnum inverter telemetry stale" in condition for condition in snapshot.status_conditions),
            snapshot.status_conditions,
        )

    def test_expired_reading_drops_value_but_keeps_warning(self) -> None:
        expired = datetime.now(timezone.utc) - timedelta(seconds=400)
        supervisor = self._supervisor_with_readers(
            {"magnum": FakeReader("magnum", "old-value", expired, stale_after_s=20.0, expire_after_s=300.0)}
        )

        snapshot = supervisor.read_snapshot()

        self.assertIsNone(snapshot.magnum)
        self.assertTrue(
            any("Magnum inverter telemetry stale" in condition for condition in snapshot.status_conditions),
            snapshot.status_conditions,
        )

    def test_ambient_error_means_no_reading_not_stale_carryover(self) -> None:
        now = datetime.now(timezone.utc)
        supervisor = self._supervisor_with_readers(
            {"ambient": FakeReader("ambient", "old-temp", now, error="probe disconnected")}
        )

        snapshot = supervisor.read_snapshot()

        self.assertIsNone(snapshot.ambient)
        self.assertEqual(snapshot.errors, [])

    def test_partial_battery_snapshot_counts_as_failed_read(self) -> None:
        # A sparse frame burst decodes to a snapshot without measurements;
        # it must not overwrite the cached good snapshot.
        sparse = PylonCanSnapshot()
        with self.assertRaisesRegex(RuntimeError, "partial CAN read"):
            validated_battery_snapshot(sparse)

        rich = PylonCanSnapshot(
            measurements=PylonMeasurements(voltage_v=53.0, current_a=-2.5, temperature_c=20.0)
        )
        self.assertIs(validated_battery_snapshot(rich), rich)

    def test_classic_write_routes_through_reader_thread(self) -> None:
        write_threads: list[str] = []

        class FakeClassic:
            def read(self):
                return ("telemetry", "settings")

            def write_charge_settings(self, **kwargs):
                write_threads.append(threading.current_thread().name)

        supervisor = Supervisor(classic=FakeClassic())
        supervisor.start_readers(interval_s=60.0)
        try:
            supervisor.wait_for_initial_readings(timeout_s=5.0)
            supervisor.write_classic_charge_settings(battery_current_limit_a=40.0)
        finally:
            supervisor.stop_readers()

        self.assertEqual(write_threads, ["reader-classic"])


if __name__ == "__main__":
    unittest.main()
