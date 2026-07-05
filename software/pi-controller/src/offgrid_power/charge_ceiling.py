"""Resolve the net battery charge-current allowance for allocation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import threading
import time

from .canbus import PylonCanSnapshot, PylonRequestFlags

# The CCL scaling factor is an operator knob (it scales the BMS charge-current
# limit down to a working budget near the taper knee). Keep it inside a sane
# band: never starve charging to nothing, never claim more than the BMS allows.
MIN_CCL_SCALING_FACTOR = 0.05
MAX_CCL_SCALING_FACTOR = 1.0


@dataclass(frozen=True)
class ChargeCeilingConfig:
    bms_knee_ccl_baseline_a: float = 200.0
    bms_ccl_scaling_factor: float = 0.5
    full_soc_percent: float = 100.0
    full_reset_soc_percent: float = 98.0
    full_reset_voltage_v: float = 54.0
    high_cell_stop_v: float = 3.62
    high_cell_soft_limit_v: float = 3.55
    high_delta_stop_mv: float = 150.0
    low_temp_stop_c: float = 0.0
    low_temp_recover_c: float = 2.0


@dataclass(frozen=True)
class ChargeCeilingResult:
    # None means unconstrained. A positive number is the resolved net charge
    # allowance. 0 means hard stop.
    ceiling_a: float | None
    reason: str


class ChargeCeiling:
    """Stateful charge-allowance resolver.

    One calculation owns both the soft charge allowance and hard stops:
    ``None`` releases constraints, positive amps constrain net battery charge,
    and 0 stops charging.
    """

    def __init__(
        self,
        config: ChargeCeilingConfig | None = None,
        *,
        on_scaling_change: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config or ChargeCeilingConfig()
        self._full_latched = False
        self._cell_latched = False
        self._cell_latch_reason: str | None = None
        self._low_temp_latched = False
        self._low_temp_latch_reason: str | None = None
        # Serializes operator scaling-factor edits (HTTP handler threads)
        # against each other; evaluate() reads self.config, which is rebound
        # atomically, so the allocator loop never sees a torn value.
        self._scaling_lock = threading.Lock()
        # Called with the new factor after each successful edit, so the owner can
        # persist it. Fired outside the lock so file I/O can't stall a nudge.
        self._on_scaling_change = on_scaling_change

    @property
    def scaling_factor(self) -> float:
        return self.config.bms_ccl_scaling_factor

    def set_scaling_factor(self, fraction: float) -> float:
        """Set the CCL scaling factor to an absolute value; return what stuck.

        Raises ValueError if the request is outside the allowed band rather than
        silently clamping, so a fat-fingered absolute set is refused, not masked.
        """
        rounded = _validate_scaling_factor(fraction)
        with self._scaling_lock:
            self.config = replace(self.config, bms_ccl_scaling_factor=rounded)
        self._notify_scaling_change(rounded)
        return rounded

    def nudge_scaling_factor(self, delta: float) -> tuple[float, float]:
        """Read-modify-write the CCL scaling factor by `delta`.

        Returns ``(previous, new)``. Raises ValueError if the result would leave
        the allowed band. The read and write happen under one lock so concurrent
        nudges can't interleave.
        """
        with self._scaling_lock:
            previous = self.config.bms_ccl_scaling_factor
            new = _validate_scaling_factor(previous + delta)
            self.config = replace(self.config, bms_ccl_scaling_factor=new)
        self._notify_scaling_change(new)
        return round(previous, 4), new

    def _notify_scaling_change(self, value: float) -> None:
        if self._on_scaling_change is not None:
            self._on_scaling_change(value)

    @property
    def full_latched(self) -> bool:
        return self._full_latched

    @property
    def cell_latched(self) -> bool:
        return self._cell_latched

    @property
    def low_temp_latched(self) -> bool:
        return self._low_temp_latched

    def evaluate(
        self,
        battery: PylonCanSnapshot | None,
        *,
        charge_enabled: bool = True,
    ) -> ChargeCeilingResult:
        config = self.config
        if battery is None:
            return ChargeCeilingResult(None, "no battery telemetry")

        if not charge_enabled:
            return ChargeCeilingResult(0.0, "BMS charge disabled")

        bms_ccl_a = (
            battery.charge_limits.charge_current_limit_a
            if battery.charge_limits is not None
            else None
        )
        if bms_ccl_a is None:
            return ChargeCeilingResult(None, "missing BMS CCL")
        if bms_ccl_a <= 0.0:
            return ChargeCeilingResult(0.0, "BMS CCL is zero")

        soc = battery.state_of_charge.soc_percent if battery.state_of_charge is not None else None
        voltage = battery.measurements.voltage_v if battery.measurements is not None else None
        extended = battery.extended_measurements
        max_cell_v = extended.max_cell_voltage_v if extended is not None else None
        min_cell_v = extended.min_cell_voltage_v if extended is not None else None
        min_temp_c = _minimum_battery_temperature_c(battery)

        # Hard safety stops first. These latch with separate recovery thresholds
        # so charger coils do not chatter around the trip point.
        low_temp_stop_reason = None
        if min_temp_c is not None and min_temp_c <= config.low_temp_stop_c:
            low_temp_stop_reason = f"battery temp {min_temp_c:.1f}C <= {config.low_temp_stop_c:.1f}C"
        if low_temp_stop_reason is not None:
            self._low_temp_latched = True
            self._low_temp_latch_reason = low_temp_stop_reason
        elif (
            self._low_temp_latched
            and min_temp_c is not None
            and min_temp_c >= config.low_temp_recover_c
        ):
            self._low_temp_latched = False
            self._low_temp_latch_reason = None
        if self._low_temp_latched:
            return ChargeCeilingResult(0.0, self._low_temp_latch_reason or "low temperature latch")

        # Cell-voltage safety stops latch until the highest cell falls below the
        # soft limit; otherwise the charger coil can chatter as the cell bobs
        # across the hard threshold by a few millivolts.
        cell_stop_reason = None
        if max_cell_v is not None and max_cell_v >= config.high_cell_stop_v:
            cell_stop_reason = f"max cell {max_cell_v:.3f}V >= {config.high_cell_stop_v:.3f}V"
        if min_cell_v is not None and max_cell_v is not None:
            delta_mv = (max_cell_v - min_cell_v) * 1000
            if delta_mv >= config.high_delta_stop_mv and max_cell_v >= config.high_cell_soft_limit_v:
                cell_stop_reason = f"cell delta {delta_mv:.0f}mV >= {config.high_delta_stop_mv:.0f}mV"
        if cell_stop_reason is not None:
            self._cell_latched = True
            self._cell_latch_reason = cell_stop_reason
        elif self._cell_latched and max_cell_v is not None and max_cell_v < config.high_cell_soft_limit_v:
            self._cell_latched = False
            self._cell_latch_reason = None
        if self._cell_latched:
            return ChargeCeilingResult(0.0, self._cell_latch_reason or "cell safety latch")

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

        if bms_ccl_a >= config.bms_knee_ccl_baseline_a:
            return ChargeCeilingResult(None, "unconstrained")

        allowance_a = max(
            0.0,
            min(bms_ccl_a, bms_ccl_a * config.bms_ccl_scaling_factor),
        )
        return ChargeCeilingResult(round(allowance_a, 1), "BMS CCL fraction")


@dataclass(frozen=True)
class ChargeEnableResolution:
    """Resolved BMS charge-enable command.

    ``degraded`` is True when the resolver had no fresh request-flags frame and is
    running on a held/defaulted value -- the signal for health to go loud.
    """

    charge_enabled: bool
    degraded: bool
    reason: str


class ChargeEnableResolver:
    """Fault-tolerant resolver for the BMS charge-enable request.

    The Pylon 0x35C request-flags frame carries the charge-enable bit -- the
    primary charge gate. A single dropped frame makes ``request_flags`` None, and
    treating that momentary gap as an explicit "stop" pulses charge off. Worse, a
    *sustained* stop is the dangerous failure for an off-grid pack: it walks the
    bank to blackout and takes the supervisor (and its watchdog) down with it.
    Disable-on-blindness is therefore not the conservative default -- releasing to
    the charge controllers' own regulation is, because:

      * both controllers self-regulate to a conservative absorb/float voltage well
        below the BMS charge-voltage limit, so they cannot overcharge, and
      * the BMS keeps independent hardware over-voltage and under-temperature
        cutoffs regardless of this request bit or the Pi.

    Note this only governs the BMS's *explicit* charge-enable bit. The cell,
    temperature, and full-charge safety stops in :class:`ChargeCeiling` run off
    whatever battery frames ARE present, so releasing here never bypasses them.

    Policy (see docs/subsystems/charge-controller.md):

      * flag present         -> use it verbatim (act on a genuine stop at once) and
                                latch it as the last-known-good value.
      * flag absent < hold_s -> hold last-known-good (debounce a dropped frame).
      * flag absent >= hold_s -> RELEASE (enabled) so charging reverts to autonomous
        (sustained blindness)   control, UNLESS an independent, CAN-agnostic ambient
                                temperature is at/below ``cold_release_block_c`` (a
                                blind release could cold-charge) -- then hold off.
                                Marked ``degraded`` either way.
    """

    def __init__(
        self,
        *,
        hold_s: float = 45.0,
        cold_release_block_c: float = 2.0,
    ) -> None:
        self.hold_s = max(0.0, hold_s)
        self.cold_release_block_c = cold_release_block_c
        # Optimistic seed: with no BMS command seen yet, default to enabled. The
        # ceiling's own safety stops still run off present battery frames, so this
        # governs only the explicit request bit.
        self._last_enable: bool = True
        self._last_seen_monotonic: float | None = None

    @property
    def last_enable(self) -> bool:
        return self._last_enable

    def resolve(
        self,
        request_flags: PylonRequestFlags | None,
        *,
        ambient_c: float | None = None,
        now: float | None = None,
    ) -> ChargeEnableResolution:
        current = time.monotonic() if now is None else now
        if request_flags is not None:
            self._last_enable = request_flags.charge_enable
            self._last_seen_monotonic = current
            return ChargeEnableResolution(request_flags.charge_enable, False, "bms request")

        # Request-flags frame absent for this read.
        if (
            self._last_seen_monotonic is not None
            and (current - self._last_seen_monotonic) < self.hold_s
        ):
            return ChargeEnableResolution(
                self._last_enable, False, "hold last-known charge-enable (frame gap)"
            )

        # Sustained blindness: revert to autonomous controller regulation unless it
        # is cold enough that a blind release risks cold-charging the pack.
        if ambient_c is not None and ambient_c <= self.cold_release_block_c:
            return ChargeEnableResolution(
                False, True, f"blind + ambient {ambient_c:.1f}C <= {self.cold_release_block_c:.1f}C: hold off"
            )
        return ChargeEnableResolution(
            True, True, "blind: release to controller regulation"
        )


def _validate_scaling_factor(fraction: float) -> float:
    rounded = round(fraction, 4)
    if not (MIN_CCL_SCALING_FACTOR <= rounded <= MAX_CCL_SCALING_FACTOR):
        raise ValueError(
            f"CCL scaling factor out of range: {rounded:.2f} "
            f"(allowed {MIN_CCL_SCALING_FACTOR:.2f}-{MAX_CCL_SCALING_FACTOR:.2f})"
        )
    return rounded


def _minimum_battery_temperature_c(battery: PylonCanSnapshot) -> float | None:
    """Most conservative charge-temperature signal available.

    The extended CAN frames expose min/max cell temperature candidates. Use the
    minimum cell temperature when present; fall back to the pack temperature from
    the ordinary measurements frame so the guard still works if extended frames
    are temporarily absent.
    """
    extended = battery.extended_measurements
    if extended is not None and extended.min_cell_temperature_c is not None:
        return extended.min_cell_temperature_c
    if battery.measurements is not None:
        return battery.measurements.temperature_c
    return None
