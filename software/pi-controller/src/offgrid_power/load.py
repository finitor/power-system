"""Load estimates derived from battery and charge-controller telemetry."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock

from .classic import ClassicTelemetry
from .canbus import PylonCanSnapshot
from .supervisor import SupervisorSnapshot


MIDNIGHT_SOC_UNAVAILABLE = "unavailable, midnight SOC was not logged"
ROLLING_LOAD_WINDOW = timedelta(hours=3)


@dataclass(frozen=True)
class LoadTotals:
    current_a: float
    power_w: float
    consumed_ah: float
    consumed_percent: float


class LoadTotalsTracker:
    def __init__(self, battery_capacity_ah: float = 200.0) -> None:
        self.battery_capacity_ah = battery_capacity_ah
        self._day: date | None = None
        self._last_sample_at: datetime | None = None
        self._consumed_ah = 0.0

    def update(
        self,
        captured_at: datetime,
        battery: PylonCanSnapshot | None,
        classic: ClassicTelemetry | None,
    ) -> LoadTotals | None:
        if battery is None or battery.measurements is None or classic is None:
            self._last_sample_at = captured_at
            return None

        local_day = captured_at.astimezone().date()
        if self._day != local_day:
            self._day = local_day
            self._last_sample_at = None
            self._consumed_ah = 0.0

        measurements = battery.measurements
        current_a = classic.battery_current_a - measurements.current_a
        power_w = classic.battery_power_w - (measurements.voltage_v * measurements.current_a)

        if self._last_sample_at is not None:
            elapsed_h = max(0.0, (captured_at - self._last_sample_at).total_seconds() / 3600)
            self._consumed_ah += max(0.0, current_a) * elapsed_h
        self._last_sample_at = captured_at

        consumed_percent = 0.0
        if self.battery_capacity_ah > 0:
            consumed_percent = self._consumed_ah / self.battery_capacity_ah * 100

        return LoadTotals(
            current_a=current_a,
            power_w=power_w,
            consumed_ah=self._consumed_ah,
            consumed_percent=consumed_percent,
        )


@dataclass(frozen=True)
class LoadSummary:
    current_a: float
    power_w: int
    average_today_text: str | None = None
    today_text: str | None = None
    remaining_text: str | None = None
    rolling_average_a: float | None = None
    rolling_average_w: float | None = None


@dataclass(frozen=True)
class LoadSample:
    captured_at: datetime
    current_a: float
    power_w: int
    soc_percent: int | None = None
    voltage_v: float | None = None


class LoadTracker:
    def __init__(
        self,
        midnight_soc_log_path: str | None = "data/load-soc-baselines.csv",
        sample_buffer: "LoadSampleBuffer | None" = None,
    ) -> None:
        self.midnight_soc_log_path = Path(midnight_soc_log_path) if midnight_soc_log_path else None
        self._midnight_soc_by_day: dict[str, int] | None = None
        self.sample_buffer = sample_buffer

    def update(self, snapshot: SupervisorSnapshot) -> LoadSummary | None:
        current_a = estimate_load_current_a(snapshot)
        if current_a is None:
            return None

        voltage_v = load_voltage_v(snapshot)
        capacity_ah = bank_capacity_ah(snapshot)
        midnight_soc = self._midnight_soc_for_snapshot(snapshot)
        current_summary = LoadSummary(
            current_a=current_a,
            power_w=round(current_a * voltage_v),
        )
        rolling_average = None
        if self.sample_buffer is not None:
            self.sample_buffer.append(snapshot, current_summary)
            rolling_average = self.sample_buffer.rolling_average(
                now=snapshot.captured_at,
                window=ROLLING_LOAD_WINDOW,
            )
        summary = LoadSummary(
            current_a=current_a,
            power_w=current_summary.power_w,
            average_today_text=rolling_load_average_text(rolling_average),
            today_text=estimate_load_today_text(snapshot, capacity_ah, midnight_soc),
            remaining_text=estimate_load_remaining_from_average_a(
                snapshot,
                capacity_ah,
                None if rolling_average is None else rolling_average[0],
            ),
            rolling_average_a=None if rolling_average is None else rolling_average[0],
            rolling_average_w=None if rolling_average is None else rolling_average[1],
        )
        return summary

    def _midnight_soc_for_snapshot(self, snapshot: SupervisorSnapshot) -> int | None:
        if snapshot.battery is None or snapshot.battery.state_of_charge is None:
            return None

        captured_at = snapshot.captured_at.astimezone()
        day = captured_at.date().isoformat()
        midnight_soc = self._read_midnight_soc_by_day().get(day)
        if midnight_soc is not None:
            return midnight_soc

        if _seconds_since_midnight(captured_at) <= 300:
            midnight_soc = snapshot.battery.state_of_charge.soc_percent
            self._write_midnight_soc(day, captured_at, midnight_soc)
            return midnight_soc
        return None

    def _read_midnight_soc_by_day(self) -> dict[str, int]:
        if self._midnight_soc_by_day is not None:
            return self._midnight_soc_by_day

        self._midnight_soc_by_day = {}
        if self.midnight_soc_log_path is None or not self.midnight_soc_log_path.exists():
            return self._midnight_soc_by_day

        with self.midnight_soc_log_path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    self._midnight_soc_by_day[row["day"]] = int(row["soc_percent"])
                except (KeyError, ValueError):
                    continue
        return self._midnight_soc_by_day

    def _write_midnight_soc(self, day: str, captured_at: datetime, soc_percent: int) -> None:
        if self.midnight_soc_log_path is None:
            return

        baselines = self._read_midnight_soc_by_day()
        if day in baselines:
            return

        self.midnight_soc_log_path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not self.midnight_soc_log_path.exists() or self.midnight_soc_log_path.stat().st_size == 0
        with self.midnight_soc_log_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if needs_header:
                writer.writerow(["day", "captured_at", "soc_percent"])
            writer.writerow([day, captured_at.isoformat(), soc_percent])
        baselines[day] = soc_percent


class LoadSampleBuffer:
    FIELDNAMES = ["captured_at", "current_a", "power_w", "soc_percent", "voltage_v"]

    def __init__(
        self,
        path: str | None = "data/load-samples.csv",
        retention: timedelta = timedelta(hours=24),
        prune_interval: timedelta = timedelta(minutes=5),
    ) -> None:
        self.path = Path(path) if path else None
        self.retention = retention
        self.prune_interval = prune_interval
        self._last_prune_at: datetime | None = None
        self._lock = Lock()

    def append(self, snapshot: SupervisorSnapshot, summary: LoadSummary) -> None:
        if self.path is None:
            return

        sample = LoadSample(
            captured_at=snapshot.captured_at.astimezone(),
            current_a=summary.current_a,
            power_w=summary.power_w,
            soc_percent=_snapshot_soc_percent(snapshot),
            voltage_v=load_voltage_v(snapshot),
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            needs_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
                if needs_header:
                    writer.writeheader()
                writer.writerow(self._sample_row(sample))
            if self._should_prune(sample.captured_at):
                self._prune_locked(sample.captured_at)

    def samples(self, now: datetime | None = None, window: timedelta | None = None) -> list[LoadSample]:
        if self.path is None or not self.path.exists():
            return []

        reference = (now or datetime.now().astimezone()).astimezone()
        cutoff = reference - (window if window is not None else self.retention)
        with self._lock:
            return [sample for sample in self._read_samples_locked() if sample.captured_at >= cutoff]

    def rolling_average(self, now: datetime | None = None, window: timedelta = timedelta(hours=1)) -> tuple[float, float] | None:
        samples = self.samples(now=now, window=window)
        if not samples:
            return None
        average_a = sum(sample.current_a for sample in samples) / len(samples)
        average_w = sum(sample.power_w for sample in samples) / len(samples)
        return average_a, average_w

    def prune(self, now: datetime | None = None) -> None:
        if self.path is None:
            return
        reference = (now or datetime.now().astimezone()).astimezone()
        with self._lock:
            self._prune_locked(reference)

    def _should_prune(self, captured_at: datetime) -> bool:
        if self._last_prune_at is None:
            return True
        return captured_at - self._last_prune_at >= self.prune_interval

    def _prune_locked(self, now: datetime) -> None:
        if self.path is None or not self.path.exists():
            return

        cutoff = now - self.retention
        samples = [sample for sample in self._read_samples_locked() if sample.captured_at >= cutoff]
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
            writer.writeheader()
            for sample in samples:
                writer.writerow(self._sample_row(sample))
        self._last_prune_at = now

    def _read_samples_locked(self) -> list[LoadSample]:
        if self.path is None or not self.path.exists():
            return []

        samples: list[LoadSample] = []
        with self.path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                sample = self._sample_from_row(row)
                if sample is not None:
                    samples.append(sample)
        return samples

    def _sample_row(self, sample: LoadSample) -> dict[str, str]:
        return {
            "captured_at": sample.captured_at.isoformat(),
            "current_a": f"{sample.current_a:.3f}",
            "power_w": str(sample.power_w),
            "soc_percent": "" if sample.soc_percent is None else str(sample.soc_percent),
            "voltage_v": "" if sample.voltage_v is None else f"{sample.voltage_v:.3f}",
        }

    def _sample_from_row(self, row: dict[str, str]) -> LoadSample | None:
        try:
            captured_at = datetime.fromisoformat(row["captured_at"]).astimezone()
            soc_text = row.get("soc_percent", "")
            voltage_text = row.get("voltage_v", "")
            return LoadSample(
                captured_at=captured_at,
                current_a=float(row["current_a"]),
                power_w=int(row["power_w"]),
                soc_percent=None if not soc_text else int(soc_text),
                voltage_v=None if not voltage_text else float(voltage_text),
            )
        except (KeyError, TypeError, ValueError):
            return None


def estimate_load_current_a(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.classic is None or snapshot.battery is None or snapshot.battery.measurements is None:
        return None
    # Classic current is charger output. BMS current is net battery current,
    # positive while charging and negative while discharging.
    return snapshot.classic.battery_current_a - snapshot.battery.measurements.current_a


def load_voltage_v(snapshot: SupervisorSnapshot) -> float:
    if snapshot.battery is not None and snapshot.battery.measurements is not None:
        return snapshot.battery.measurements.voltage_v
    if snapshot.classic is not None:
        return snapshot.classic.battery_voltage_v
    return 0.0


def _snapshot_soc_percent(snapshot: SupervisorSnapshot) -> int | None:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return None
    return snapshot.battery.state_of_charge.soc_percent


def load_today_text(today_ah: float, bank_percent: float | None) -> str:
    text = f"{today_ah:.1f}Ah"
    if bank_percent is not None:
        text += f" {bank_percent:.1f}% of bank"
    return text


def estimate_load_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if today_ah is None:
        return MIDNIGHT_SOC_UNAVAILABLE
    return load_today_text(today_ah, today_ah / bank_capacity * 100)


def estimate_load_today_ah(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> float | None:
    if (
        snapshot.classic is None
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None
    if midnight_soc_percent is None:
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    battery_delta_ah = (current_soc_percent - midnight_soc_percent) / 100 * bank_capacity
    return snapshot.classic.daily_amp_hours_ah - battery_delta_ah


def estimate_load_average_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str | None:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if today_ah is None:
        return None

    elapsed_hours = _seconds_since_midnight(snapshot.captured_at.astimezone()) / 3600
    if elapsed_hours <= 0:
        return None

    average_a = today_ah / elapsed_hours
    average_w = round(average_a * load_voltage_v(snapshot))
    return f"{average_a:.1f}A  {average_w}W"


def estimate_load_remaining_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str | None:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if (
        today_ah is None
        or today_ah <= 0
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None

    elapsed_hours = _seconds_since_midnight(snapshot.captured_at.astimezone()) / 3600
    if elapsed_hours <= 0:
        return None

    average_load_a = today_ah / elapsed_hours
    if average_load_a <= 0:
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    remaining_ah = current_soc_percent / 100 * bank_capacity
    remaining_hours = remaining_ah / average_load_a
    return f"{remaining_hours:.1f}h"


def rolling_load_average_text(rolling_average: tuple[float, float] | None) -> str | None:
    if rolling_average is None:
        return None
    average_a, average_w = rolling_average
    return f"{average_a:.1f}A  {round(average_w)}W"


def estimate_load_remaining_from_average_a(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    average_load_a: float | None,
) -> str | None:
    if (
        average_load_a is None
        or average_load_a <= 0
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    remaining_ah = current_soc_percent / 100 * bank_capacity
    remaining_hours = remaining_ah / average_load_a
    return f"{remaining_hours:.1f}h"


def bank_capacity_ah(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.battery is None or snapshot.battery.extended_measurements is None:
        return None
    return snapshot.battery.extended_measurements.installed_capacity_ah


def _seconds_since_midnight(value: datetime) -> float:
    return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000
