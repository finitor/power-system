"""Per-device actor threads: all I/O for a device on one thread.

Each device adapter gets a dedicated thread that owns the adapter
exclusively. Periodic reads happen on that thread's poll loop, and writes
are submitted to the same thread as queued commands — so reads and writes
to one device can never race each other, and one slow or wedged device
(Modbus timeout, serial open, CAN collection window) cannot starve the
supervisor tick, the displays, or control decisions.

The reader keeps the last good value with its capture time; consumers
decide what staleness means for them.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
import queue
from threading import Event, Lock, Thread
import time
from typing import Callable


# Upper bound on how long the actor loop blocks waiting for commands, so it
# notices stop() promptly even with long poll intervals.
_COMMAND_WAIT_SLICE_S = 0.5


@dataclass(frozen=True)
class DeviceReading:
    """A point-in-time view of a reader's cache."""

    name: str
    value: object | None          # last good value; survives later failures
    captured_at: datetime | None  # when the last good value was read
    error: str | None             # error from the most recent attempt, else None
    stale_after_s: float

    def age_seconds(self, now: datetime | None = None) -> float | None:
        if self.captured_at is None:
            return None
        reference = now or datetime.now(timezone.utc)
        return max(0.0, (reference - self.captured_at).total_seconds())

    def is_stale(self, now: datetime | None = None) -> bool:
        age = self.age_seconds(now)
        if age is None:
            # Never read successfully: not "stale", just absent.
            return False
        return age > self.stale_after_s


class PollingReader:
    """Device actor: polls read_fn on its own thread, executes submitted
    commands (e.g. writes) on that same thread between polls.

    A read_fn that raises, or returns None, counts as a failure: the error is
    recorded and the previous good value is retained. Readings are accessed
    via reading(), which returns an immutable copy.
    """

    def __init__(
        self,
        name: str,
        read_fn: Callable[[], object],
        interval_s: float = 5.0,
        stale_after_s: float | None = None,
    ) -> None:
        self.name = name
        self.interval_s = interval_s
        self.stale_after_s = stale_after_s if stale_after_s is not None else interval_s * 4
        self._read_fn = read_fn
        self._lock = Lock()
        self._value: object | None = None
        self._captured_at: datetime | None = None
        self._error: str | None = None
        self._commands: queue.Queue[tuple[Callable[[], object], Future]] = queue.Queue()
        self._stop = Event()
        self._thread: Thread | None = None

    def read_now(self) -> None:
        """Perform one read attempt synchronously (also used by the thread loop)."""
        try:
            value = self._read_fn()
        except Exception as exc:  # noqa: BLE001 - reader must survive any adapter failure.
            with self._lock:
                self._error = str(exc) or type(exc).__name__
            return
        if value is None:
            with self._lock:
                self._error = "no reading"
            return
        with self._lock:
            self._value = value
            self._captured_at = datetime.now(timezone.utc)
            self._error = None

    def reading(self) -> DeviceReading:
        with self._lock:
            return DeviceReading(
                name=self.name,
                value=self._value,
                captured_at=self._captured_at,
                error=self._error,
                stale_after_s=self.stale_after_s,
            )

    def submit(self, fn: Callable[[], object], timeout_s: float = 10.0) -> object:
        """Run fn on the device thread and return its result.

        This is how writes reach the device: the actor thread is the only
        code that ever touches the adapter, so a submitted write can never
        race a poll. Blocks the caller until the command completes; raises
        whatever fn raised.
        """
        if self._thread is None:
            # Actor not running: execute inline (single-threaded mode).
            return fn()
        future: Future = Future()
        self._commands.put((fn, future))
        return future.result(timeout=timeout_s)

    def request_refresh(self) -> None:
        """Queue an out-of-cycle poll on the device thread; returns at once.

        Fire-and-forget: the actor thread runs it between polls (waking from
        the command queue near-instantly), so a slow or wedged adapter can
        never block the caller. The fresh value lands on a later reading().
        """
        if self._thread is None:
            self.read_now()
            return
        self._commands.put((self.read_now, Future()))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name=f"reader-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def _run(self) -> None:
        next_read_at = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_read_at:
                self.read_now()
                next_read_at = time.monotonic() + self.interval_s

            # Service commands until the next poll is due, in slices short
            # enough to notice stop().
            wait_s = min(max(0.0, next_read_at - time.monotonic()), _COMMAND_WAIT_SLICE_S)
            try:
                fn, future = self._commands.get(timeout=wait_s)
            except queue.Empty:
                continue
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except Exception as exc:  # noqa: BLE001 - deliver the failure to the submitter.
                future.set_exception(exc)
