"""Load estimates derived from battery and charge-controller telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .classic import ClassicTelemetry
from .canbus import PylonCanSnapshot


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
