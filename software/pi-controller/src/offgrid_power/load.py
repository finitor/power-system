"""Load estimates derived from battery and charge-controller telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Callable

from .classic import ClassicTelemetry
from .canbus import PylonCanSnapshot
from .supervisor import SupervisorSnapshot


MIDNIGHT_SOC_UNAVAILABLE = "unavailable, midnight SOC was not logged"
LIVE_SOC_UNAVAILABLE = "unavailable, battery SOC offline"
ROLLING_LOAD_WINDOW = timedelta(hours=3)
# Nominal pack voltage for converting an SOC (coulomb) change to energy. Only the
# battery-gain term of the daily energy balance uses it; a 16S LiFePO4 nominal,
# a few-% effect since the bank operates 51-56 V.
NOMINAL_PACK_VOLTAGE_V = 51.2


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
        midnight_soc_provider: Callable[[date], int | None] | None = None,
        sample_buffer: "LoadSampleBuffer | None" = None,
    ) -> None:
        self.midnight_soc_provider = midnight_soc_provider
        self._midnight_soc_by_day: dict[str, int | None] = {}
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
        if day in self._midnight_soc_by_day:
            return self._midnight_soc_by_day[day]

        if _seconds_since_midnight(captured_at) <= 300:
            midnight_soc = snapshot.battery.state_of_charge.soc_percent
            self._midnight_soc_by_day[day] = midnight_soc
            return midnight_soc

        # The supervisor's metric store keeps the SOC history; ask it once
        # per day (a miss means the supervisor was down over midnight, and
        # the store cannot gain a midnight sample retroactively).
        midnight_soc = None
        if self.midnight_soc_provider is not None:
            midnight_soc = self.midnight_soc_provider(captured_at.date())
        self._midnight_soc_by_day[day] = midnight_soc
        return midnight_soc


class LoadSampleBuffer:
    """In-memory rolling load samples for the rolling-average display.

    The durable copy lives in the metric store (source 'load'); seed() from
    there at startup restores the rolling window across restarts.
    """

    def __init__(
        self,
        retention: timedelta = timedelta(hours=24),
    ) -> None:
        self.retention = retention
        self._samples: deque[LoadSample] = deque()
        self._lock = Lock()

    def seed(self, samples: list[LoadSample]) -> None:
        with self._lock:
            for sample in sorted(samples, key=lambda sample: sample.captured_at):
                self._samples.append(sample)
            if self._samples:
                self._prune_locked(self._samples[-1].captured_at)

    def append(self, snapshot: SupervisorSnapshot, summary: LoadSummary) -> None:
        sample = LoadSample(
            captured_at=snapshot.captured_at.astimezone(),
            current_a=summary.current_a,
            power_w=summary.power_w,
            soc_percent=_snapshot_soc_percent(snapshot),
            voltage_v=load_voltage_v(snapshot),
        )
        with self._lock:
            self._samples.append(sample)
            self._prune_locked(sample.captured_at)

    def samples(self, now: datetime | None = None, window: timedelta | None = None) -> list[LoadSample]:
        reference = (now or datetime.now().astimezone()).astimezone()
        cutoff = reference - (window if window is not None else self.retention)
        with self._lock:
            return [sample for sample in self._samples if sample.captured_at >= cutoff]

    def rolling_average(self, now: datetime | None = None, window: timedelta = timedelta(hours=1)) -> tuple[float, float] | None:
        samples = self.samples(now=now, window=window)
        if not samples:
            return None
        average_a = sum(sample.current_a for sample in samples) / len(samples)
        average_w = sum(sample.power_w for sample in samples) / len(samples)
        return average_a, average_w

    def _prune_locked(self, now: datetime) -> None:
        cutoff = now - self.retention
        while self._samples and self._samples[0].captured_at < cutoff:
            self._samples.popleft()


def estimate_load_current_a(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.classic is None or snapshot.battery is None or snapshot.battery.measurements is None:
        return None
    # Total consumption by a bus energy balance:
    #   load = (charge into the bus) - (net into the battery)
    # Charge controllers report output current positive into the battery, and
    # the BMS net current is positive while charging / negative while
    # discharging. Sum *every* charge source: with only the Classic counted,
    # the load reads low or negative whenever the EPEver array is also
    # contributing (its output is omitted from charge-in while its share of
    # the battery gain still lands in the BMS net).
    charge_in_a = snapshot.classic.battery_current_a
    if snapshot.epever is not None:
        charge_in_a += snapshot.epever.battery_current_a
    # The Magnum is a third potential source, but only when the generator is
    # running and it is charging (inverter.charger_on). While inverting it is
    # the dominant *load*, and that draw is already reflected in the BMS net
    # current -- so it cancels in this balance and must NOT be added here, or
    # it would double-count. Its DC-amp sign is also inverted vs the
    # controllers (reads positive while inverting) and unverified under charge,
    # so the generator-charge source term is intentionally deferred until that
    # sign is confirmed on a running generator.
    return charge_in_a - snapshot.battery.measurements.current_a


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


def _load_today_energy_text(kwh: float, bank_percent: float | None) -> str:
    wh = kwh * 1000
    text = f"{round(wh)}Wh" if abs(wh) < 1000 else f"{kwh:.1f}kWh"
    if bank_percent is not None:
        text += f" {bank_percent:.0f}% of bank"
    return text


def estimate_load_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
    nominal_voltage_v: float = NOMINAL_PACK_VOLTAGE_V,
) -> str:
    # Attribute the failure honestly: missing live battery data is not the
    # same condition as a missing midnight baseline.
    if _snapshot_soc_percent(snapshot) is None or bank_capacity is None or bank_capacity <= 0:
        return LIVE_SOC_UNAVAILABLE
    if midnight_soc_percent is None:
        return MIDNIGHT_SOC_UNAVAILABLE
    today_kwh = estimate_load_today_kwh(snapshot, bank_capacity, midnight_soc_percent, nominal_voltage_v)
    if today_kwh is None:
        return LIVE_SOC_UNAVAILABLE
    capacity_kwh = bank_capacity * nominal_voltage_v / 1000
    bank_percent = today_kwh / capacity_kwh * 100 if capacity_kwh > 0 else None
    return _load_today_energy_text(today_kwh, bank_percent)


def estimate_load_today_kwh(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
    nominal_voltage_v: float = NOMINAL_PACK_VOLTAGE_V,
) -> float | None:
    """Energy consumed since local midnight, by bus balance:

        consumed = Σ(producer daily energy) - battery energy gained

    Producers report battery-side daily kWh (Classic native, EPEver derived from
    its lifetime counter). The battery-gain term converts the SOC change to
    energy at a nominal pack voltage -- the only place voltage enters. Returns
    None (rather than undercounting) if a present producer's daily is missing."""
    if (
        snapshot.classic is None
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
        or midnight_soc_percent is None
        or snapshot.classic.daily_energy_kwh is None
    ):
        return None
    charge_in_kwh = snapshot.classic.daily_energy_kwh
    if snapshot.epever is not None:
        if snapshot.epever.generated_today_kwh is None:
            return None  # EPEver daily not yet derivable -> don't undercount
        charge_in_kwh += snapshot.epever.generated_today_kwh
    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    battery_gain_kwh = (
        (current_soc_percent - midnight_soc_percent) / 100 * bank_capacity * nominal_voltage_v / 1000
    )
    return charge_in_kwh - battery_gain_kwh


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
