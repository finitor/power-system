"""Boot-time system clock recovery from the MidNite Classic RTC."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Callable
from zoneinfo import ZoneInfo

from pymodbus.client import ModbusTcpClient

from .classic import RegisterBlock, read_block, u32


DEFAULT_TIMESYNC_MARKER = Path("/run/systemd/timesync/synchronized")
DEFAULT_TIMEZONE = "America/Toronto"
MAX_FORWARD_STEP_SECONDS = 2 * 366 * 24 * 60 * 60
CLASSIC_CONFIRM_SECONDS = 2.0
CLASSIC_ADVANCE_TOLERANCE_SECONDS = 2.0


@dataclass(frozen=True)
class ClockRestoreResult:
    action: str
    detail: str
    classic_time: datetime | None = None
    system_time: datetime | None = None
    offset_seconds: float | None = None


def decode_classic_clock(
    block: RegisterBlock,
    timezone_name: str = DEFAULT_TIMEZONE,
    reference: datetime | None = None,
) -> datetime:
    """Decode CTIME0/CTIME1 as a site-local aware datetime.

    CTIME2 (register 4218) is deliberately ignored: the installed Classic
    reports a stale day-of-year despite correct date/time fields.
    """
    time_word = u32(block.get(4214), block.get(4215))
    date_word = u32(block.get(4216), block.get(4217))
    year = (date_word >> 16) & 0x0FFF
    month = (date_word >> 8) & 0x0F
    day = date_word & 0x1F
    hour = (time_word >> 16) & 0x1F
    minute = (time_word >> 8) & 0x3F
    second = time_word & 0x3F
    if not 2020 <= year <= 2099:
        raise ValueError(f"implausible Classic RTC year {year}")

    naive = datetime(year, month, day, hour, minute, second)
    zone = ZoneInfo(timezone_name)
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if roundtrip == naive and candidate.utcoffset() not in {
            item.utcoffset() for item in candidates
        }:
            candidates.append(candidate)
    if not candidates:
        raise ValueError(f"Classic RTC reports nonexistent local time {naive.isoformat()}")
    if len(candidates) == 1:
        return candidates[0]

    # The RTC has no DST-fold bit. During the repeated fall-back hour, select
    # the occurrence closest to the Pi's persisted approximate clock.
    reference = reference or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return min(
        candidates,
        key=lambda candidate: abs((candidate.astimezone(timezone.utc) - reference.astimezone(timezone.utc)).total_seconds()),
    )


def read_classic_clock(
    host: str,
    port: int = 502,
    device_id: int = 10,
    timeout: float = 3.0,
    timezone_name: str = DEFAULT_TIMEZONE,
    reference: datetime | None = None,
) -> datetime:
    client = ModbusTcpClient(host, port=port, timeout=timeout)
    if not client.connect():
        raise ConnectionError(f"could not connect to Classic at {host}:{port}")
    try:
        block = read_block(client, 4214, 5, device_id)
    finally:
        client.close()
    return decode_classic_clock(block, timezone_name=timezone_name, reference=reference)


def restore_system_clock(
    *,
    host: str,
    port: int = 502,
    device_id: int = 10,
    timeout: float = 3.0,
    timezone_name: str = DEFAULT_TIMEZONE,
    ntp_wait_seconds: float = 15.0,
    classic_wait_seconds: float = 120.0,
    poll_seconds: float = 1.0,
    dry_run: bool = False,
    ignore_ntp: bool = False,
    timesync_marker: Path = DEFAULT_TIMESYNC_MARKER,
    now_fn: Callable[[], datetime] | None = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
    set_clock_fn: Callable[[float], None] | None = None,
    read_clock_fn: Callable[[], datetime] | None = None,
) -> ClockRestoreResult:
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    set_clock_fn = set_clock_fn or (
        lambda timestamp: time.clock_settime(time.CLOCK_REALTIME, timestamp)
    )

    if not ignore_ntp:
        deadline = monotonic_fn() + max(0.0, ntp_wait_seconds)
        while True:
            if timesync_marker.exists():
                return ClockRestoreResult("ntp", "NTP already synchronized; Classic fallback not needed")
            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                break
            sleep_fn(min(poll_seconds, remaining))

    read_clock_fn = read_clock_fn or (
        lambda: read_classic_clock(
            host,
            port=port,
            device_id=device_id,
            timeout=timeout,
            timezone_name=timezone_name,
            reference=now_fn(),
        )
    )
    deadline = monotonic_fn() + max(0.0, classic_wait_seconds)
    last_error: Exception | None = None
    classic_time: datetime | None = None
    previous_classic_time: datetime | None = None
    previous_read_at: float | None = None
    while True:
        # NTP can become available at any point while the Classic is booting.
        # Prefer it immediately rather than making telemetry wait for the full
        # Classic readiness window.
        if not ignore_ntp and timesync_marker.exists():
            return ClockRestoreResult(
                "ntp", "NTP synchronized while waiting for Classic; Classic fallback not applied"
            )
        try:
            candidate = read_clock_fn()
            if candidate.tzinfo is None or candidate.utcoffset() is None:
                raise ValueError("Classic clock decoder returned a naive timestamp")

            read_at = monotonic_fn()
            if classic_wait_seconds <= 0:
                classic_time = candidate
                break
            if previous_classic_time is not None and previous_read_at is not None:
                observed_elapsed = read_at - previous_read_at
                rtc_elapsed = (candidate - previous_classic_time).total_seconds()
                if (
                    observed_elapsed >= CLASSIC_CONFIRM_SECONDS
                    and rtc_elapsed > 0
                    and abs(rtc_elapsed - observed_elapsed)
                    <= CLASSIC_ADVANCE_TOLERANCE_SECONDS
                ):
                    classic_time = candidate
                    break
                if (
                    rtc_elapsed <= 0
                    or abs(rtc_elapsed - observed_elapsed)
                    > CLASSIC_ADVANCE_TOLERANCE_SECONDS
                ):
                    last_error = ValueError(
                        "Classic RTC changed discontinuously while booting "
                        f"(RTC {rtc_elapsed:.1f}s, elapsed {observed_elapsed:.1f}s)"
                    )
                    # A discontinuity can be the MNGP copying its durable time
                    # to the main board. Start confirmation again from the new
                    # value rather than trusting either side of the jump.
                    previous_classic_time = candidate
                    previous_read_at = read_at
            else:
                previous_classic_time = candidate
                previous_read_at = read_at
        except Exception as exc:  # noqa: BLE001 - boot fallback retries any adapter failure.
            last_error = exc
            previous_classic_time = None
            previous_read_at = None
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            detail = last_error or "Classic RTC did not produce two advancing samples"
            return ClockRestoreResult("unavailable", f"Classic clock unavailable: {detail}")
        sleep_fn(min(poll_seconds, remaining))

    assert classic_time is not None
    classic_utc = classic_time.astimezone(timezone.utc)

    # Close the small race where timesyncd succeeds while the Modbus request is
    # in flight. Never step an NTP-disciplined clock back to the Classic.
    if not ignore_ntp and timesync_marker.exists():
        return ClockRestoreResult(
            "ntp", "NTP synchronized during Classic read; Classic fallback not applied",
            classic_time=classic_time,
        )

    system_time = now_fn().astimezone(timezone.utc)
    offset = (classic_utc - system_time).total_seconds()
    if offset <= 1.0:
        return ClockRestoreResult(
            "not-ahead",
            "Classic clock is not sufficiently ahead of the persisted system clock",
            classic_time=classic_time,
            system_time=system_time,
            offset_seconds=offset,
        )
    if offset > MAX_FORWARD_STEP_SECONDS:
        return ClockRestoreResult(
            "invalid",
            f"Classic clock is implausibly far ahead ({offset:.0f}s)",
            classic_time=classic_time,
            system_time=system_time,
            offset_seconds=offset,
        )
    if not dry_run:
        set_clock_fn(classic_utc.timestamp())
    return ClockRestoreResult(
        "dry-run" if dry_run else "restored",
        f"{'Would advance' if dry_run else 'Advanced'} system clock by {offset:.3f}s from Classic RTC",
        classic_time=classic_time,
        system_time=system_time,
        offset_seconds=offset,
    )
