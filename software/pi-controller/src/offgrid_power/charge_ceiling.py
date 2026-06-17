"""Battery-state ceiling on total charge current (top-knee taper + cell safety).

Ported from the legacy single-controller ``charger_taper`` (see
docs/charge-current-allocation.md "Sunsetting the legacy taper") so the
allocator can own the voltage/SOC taper and the safety latches. Unlike the
taper, this bounds **total net battery charge current**, not one controller's
output, and "below the knee" yields *no* ceiling (None) -- the BMS CCL and the
budget govern there. The allocator combines this with the BMS CCL via min().

NOTE: the thresholds are inherited from the single-controller, ~54 V-era taper.
With the bank now operating at 55-56 V they will over-clamp (voltage above
``top_voltage_v`` pins the ceiling to ``ramp2_low_current_a``). That is fine
while the allocator is dry-run -- the traces will show it -- but the thresholds
must be re-tuned (Phase 2) before any live write trusts this ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass

from .canbus import PylonCanSnapshot


@dataclass(frozen=True)
class ChargeCeilingConfig:
    bulk_soc_percent: float = 85.0
    ramp2_soc_percent: float = 92.0
    full_soc_percent: float = 100.0
    full_reset_soc_percent: float = 98.0
    bulk_voltage_v: float = 53.6
    ramp2_voltage_v: float = 54.4
    top_voltage_v: float = 54.8
    full_reset_voltage_v: float = 54.0
    ramp1_high_current_a: float = 30.0
    ramp1_low_current_a: float = 20.0
    ramp2_high_current_a: float = 10.0
    ramp2_low_current_a: float = 4.0
    high_cell_stop_v: float = 3.55
    high_cell_soft_limit_v: float = 3.50
    high_delta_stop_mv: float = 175.0


@dataclass(frozen=True)
class ChargeCeilingResult:
    # None means "no battery-state constraint" (below the knee); the BMS CCL and
    # budget govern. A number caps total net battery charge current; 0 stops it.
    ceiling_a: float | None
    reason: str


class ChargeCeiling:
    """Stateful battery-state charge-current ceiling (the full-charge latch is
    the state). One instance per supervisor, evaluated once per cycle."""

    def __init__(self, config: ChargeCeilingConfig | None = None) -> None:
        self.config = config or ChargeCeilingConfig()
        self._full_latched = False

    @property
    def full_latched(self) -> bool:
        return self._full_latched

    def evaluate(self, battery: PylonCanSnapshot | None) -> ChargeCeilingResult:
        config = self.config
        if battery is None:
            return ChargeCeilingResult(None, "no battery telemetry")

        soc = battery.state_of_charge.soc_percent if battery.state_of_charge is not None else None
        voltage = battery.measurements.voltage_v if battery.measurements is not None else None
        extended = battery.extended_measurements
        max_cell_v = extended.max_cell_voltage_v if extended is not None else None
        min_cell_v = extended.min_cell_voltage_v if extended is not None else None

        # Hard cell-safety stops first.
        if max_cell_v is not None and max_cell_v >= config.high_cell_stop_v:
            return ChargeCeilingResult(0.0, f"max cell {max_cell_v:.3f}V >= {config.high_cell_stop_v:.3f}V")
        if min_cell_v is not None and max_cell_v is not None:
            delta_mv = (max_cell_v - min_cell_v) * 1000
            if delta_mv >= config.high_delta_stop_mv and max_cell_v >= config.high_cell_soft_limit_v:
                return ChargeCeilingResult(0.0, f"cell delta {delta_mv:.0f}mV >= {config.high_delta_stop_mv:.0f}mV")

        # Full-charge latch: hold at zero once full until the pack rests low.
        if soc is not None and soc >= config.full_soc_percent:
            self._full_latched = True
        if self._full_latched:
            rested = (
                soc is not None
                and soc < config.full_reset_soc_percent
                and voltage is not None
                and voltage <= config.full_reset_voltage_v
            )
            if rested:
                self._full_latched = False
            else:
                return ChargeCeilingResult(0.0, "full-charge latch")

        candidates: list[float] = []
        if soc is not None:
            soc_ceiling = _ceiling_from_soc(float(soc), config)
            if soc_ceiling is not None:
                candidates.append(soc_ceiling)
        if voltage is not None:
            voltage_ceiling = _ceiling_from_voltage(float(voltage), config)
            if voltage_ceiling is not None:
                candidates.append(voltage_ceiling)
        if max_cell_v is not None and max_cell_v >= config.high_cell_soft_limit_v:
            candidates.append(config.ramp2_low_current_a)

        if not candidates:
            return ChargeCeilingResult(None, "below knee")
        return ChargeCeilingResult(round(min(candidates), 1), "top-knee taper")


def _ceiling_from_soc(soc_percent: float, config: ChargeCeilingConfig) -> float | None:
    if soc_percent >= config.full_soc_percent:
        return 0.0
    if soc_percent >= config.ramp2_soc_percent:
        return _interpolate(
            soc_percent,
            config.ramp2_soc_percent,
            config.ramp2_high_current_a,
            config.full_soc_percent,
            config.ramp2_low_current_a,
        )
    if soc_percent >= config.bulk_soc_percent:
        return _interpolate(
            soc_percent,
            config.bulk_soc_percent,
            config.ramp1_high_current_a,
            config.ramp2_soc_percent,
            config.ramp1_low_current_a,
        )
    return None  # below the knee: no SOC-derived ceiling


def _ceiling_from_voltage(voltage_v: float, config: ChargeCeilingConfig) -> float | None:
    if voltage_v >= config.top_voltage_v:
        return config.ramp2_low_current_a
    if voltage_v >= config.ramp2_voltage_v:
        return _interpolate(
            voltage_v,
            config.ramp2_voltage_v,
            config.ramp2_high_current_a,
            config.top_voltage_v,
            config.ramp2_low_current_a,
        )
    if voltage_v >= config.bulk_voltage_v:
        return _interpolate(
            voltage_v,
            config.bulk_voltage_v,
            config.ramp1_high_current_a,
            config.ramp2_voltage_v,
            config.ramp1_low_current_a,
        )
    return None  # below the knee: no voltage-derived ceiling


def _interpolate(value: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x1 == x0:
        return y1
    fraction = min(1.0, max(0.0, (value - x0) / (x1 - x0)))
    return y0 + (y1 - y0) * fraction
