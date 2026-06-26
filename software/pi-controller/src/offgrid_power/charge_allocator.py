"""System-level allocation of BMS charge-current headroom across chargers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import threading

from .metrics import TelemetryEvent


class AllocationOverride:
    """Thread-safe operator knobs for pausing allocator writes and capping per-controller current.

    Controllers are addressed by index (0 = classic, 1 = epever, ...) matching the
    order chargers are appended in ``_allocation_inputs()``.  Using indices rather
    than names keeps the API stable across hardware swaps.

    Manual limits act as a ceiling: the allocator still runs and computes targets,
    but any controller whose index has a manual limit set will be clamped to at most
    that value, and the write is forced regardless of the deadband.

    When paused, all ``should_write`` flags are suppressed — the allocator keeps
    evaluating and logging decisions, but nothing is sent to the controllers.
    """

    # Ordered names in the same sequence as _allocation_inputs builds chargers.
    # Update this list if a new charger type is added to the supervisor.
    CONTROLLER_NAMES: list[str] = ["classic", "epever"]

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused: bool = False
        self._manual_limits_a: dict[int, float] = {}

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def set_paused(self, paused: bool) -> bool:
        """Set pause state; returns previous value."""
        with self._lock:
            previous = self._paused
            self._paused = paused
            return previous

    def set_manual_limit(self, index: int, limit_a: float | None) -> float | None:
        """Set or clear a per-controller ceiling; returns previous value."""
        if index < 0 or index >= len(self.CONTROLLER_NAMES):
            raise ValueError(f"controller index {index} out of range (0–{len(self.CONTROLLER_NAMES) - 1})")
        if limit_a is not None and limit_a < 0:
            raise ValueError(f"limit_a must be >= 0, got {limit_a}")
        with self._lock:
            previous = self._manual_limits_a.get(index)
            if limit_a is None:
                self._manual_limits_a.pop(index, None)
            else:
                self._manual_limits_a[index] = limit_a
            return previous

    def status(self) -> dict:
        with self._lock:
            return {
                "paused": self._paused,
                "manual_limits_a": {str(i): self._manual_limits_a.get(i) for i in range(len(self.CONTROLLER_NAMES))},
            }

    def apply(
        self,
        targets: dict[str, ChargerAllocationTarget],
    ) -> dict[str, ChargerAllocationTarget]:
        """Apply pause and manual-ceiling overrides to a set of allocation targets.

        Returns a new dict; input is not mutated.
        """
        with self._lock:
            paused = self._paused
            limits = dict(self._manual_limits_a)

        out: dict[str, ChargerAllocationTarget] = {}
        for idx, name in enumerate(self.CONTROLLER_NAMES):
            target = targets.get(name)
            if target is None:
                continue
            if paused:
                out[name] = ChargerAllocationTarget(
                    target_current_a=target.target_current_a,
                    should_write=False,
                    reason=target.reason,
                    disable=target.disable,
                )
                continue
            if idx in limits:
                ceiling = limits[idx]
                clamped_a = min(target.target_current_a, ceiling) if target.target_current_a is not None else ceiling
                out[name] = ChargerAllocationTarget(
                    target_current_a=clamped_a,
                    should_write=True,
                    reason=f"manual_limit({ceiling}A)",
                    disable=target.disable,
                )
            else:
                out[name] = target
        # Pass through any chargers not in CONTROLLER_NAMES unchanged.
        for name, target in targets.items():
            if name not in out:
                out[name] = target
        return out


@dataclass(frozen=True)
class ChargeAllocatorConfig:
    reserve_a: float = 0.0
    feedback_tolerance_a: float = 1.0
    min_write_delta_a: float = 1.0


@dataclass(frozen=True)
class ChargerAllocationInput:
    name: str
    actual_current_a: float
    current_limit_a: float | None
    max_current_a: float
    # PV input power is retained for trace/debug context. It is deliberately not
    # used for apportionment because controller input power can collapse when a
    # prior current limit holds that controller near zero.
    pv_power_w: float | None = None
    # Smallest current the charger can actually hold. The EPEver register floors
    # at 1 A, so a sub-floor target maps to charge-disable rather than a limit
    # write; the Classic can sit at a true 0 A, so its floor is 0.
    min_current_a: float = 0.0
    online: bool = True
    enabled: bool = True
    # "active" means the charger is *able* to charge (PV present, not faulted) --
    # NOT "is currently sourcing current". The supervisor must not derive this
    # from present output, or throttling a charger to ~0 would make it ineligible
    # and it could never be handed budget back.
    active: bool = True


@dataclass(frozen=True)
class ChargerAllocationTarget:
    target_current_a: float | None
    should_write: bool
    reason: str
    # True when the charger should be turned off for a battery/control-side stop
    # rather than current-limited. Resource-side states (no PV, sleeping
    # controller) must not set this; the controller's own state machine owns
    # low-input sleep/wake behavior.
    disable: bool = False


@dataclass(frozen=True)
class ChargeAllocationDecision:
    budget_a: float | None
    bms_ccl_a: float | None
    load_allowance_a: float
    battery_current_a: float | None
    battery_charge_a: float | None
    reason: str
    targets: dict[str, ChargerAllocationTarget]
    # "equal": the apportionment split used for trace analysis. None when no
    # budget was apportioned.
    weight_basis: str | None = None
    # Resolved net charge-current allowance from ChargeCeiling. None means
    # unconstrained; 0 means stop.
    charge_ceiling_a: float | None = None


class ChargeCurrentAllocator:
    def __init__(self, config: ChargeAllocatorConfig | None = None) -> None:
        self.config = config or ChargeAllocatorConfig()

    def decide(
        self,
        *,
        bms_ccl_a: float | None,
        charge_enabled: bool,
        battery_current_a: float | None,
        load_current_a: float | None,
        chargers: list[ChargerAllocationInput],
        charge_ceiling_a: float | None = None,
        charge_ceiling_reason: str | None = None,
    ) -> ChargeAllocationDecision:
        if bms_ccl_a is None:
            return self._no_targets("missing BMS CCL", bms_ccl_a, battery_current_a, chargers)
        if battery_current_a is None:
            return self._no_targets("missing battery current", bms_ccl_a, battery_current_a, chargers)

        eligible_max_a = sum(
            charger.max_current_a
            for charger in chargers
            if charger.online and charger.enabled and charger.active and charger.max_current_a > 0
        )

        if charge_ceiling_a is None:
            targets, weight_basis = self._allocate_budget(chargers, eligible_max_a, "unconstrained")
            return ChargeAllocationDecision(
                budget_a=round(eligible_max_a, 1),
                bms_ccl_a=bms_ccl_a,
                load_allowance_a=round(max(load_current_a or 0.0, 0.0), 1),
                battery_current_a=round(battery_current_a, 1),
                battery_charge_a=max(battery_current_a, 0.0),
                reason="unconstrained",
                targets=targets,
                weight_basis=weight_basis,
                charge_ceiling_a=charge_ceiling_a,
            )

        if charge_ceiling_a <= 0.0:
            return self._zero_targets(
                charge_ceiling_reason or "charge_ceiling",
                bms_ccl_a,
                battery_current_a,
                max(load_current_a or 0.0, 0.0),
                chargers,
                charge_ceiling_a=charge_ceiling_a,
            )

        effective_ccl_a = charge_ceiling_a
        reason = charge_ceiling_reason or "charge_ceiling"

        # If every eligible charger at full output still couldn't reach the
        # battery limit, the allocator is not the binding constraint (sunlight
        # is). Impose nothing: pin each to its own max, don't apportion, don't
        # subtract reserve. This avoids needlessly throttling -- and needlessly
        # writing -- through the abundant-headroom part of the day.
        if eligible_max_a <= effective_ccl_a:
            targets, weight_basis = self._allocate_budget(chargers, eligible_max_a, "unconstrained")
            return ChargeAllocationDecision(
                budget_a=round(eligible_max_a, 1),
                bms_ccl_a=bms_ccl_a,
                load_allowance_a=round(max(load_current_a or 0.0, 0.0), 1),
                battery_current_a=round(battery_current_a, 1),
                battery_charge_a=max(battery_current_a, 0.0),
                reason="unconstrained",
                targets=targets,
                weight_basis=weight_basis,
                charge_ceiling_a=charge_ceiling_a,
            )

        load_allowance_a = max(load_current_a or 0.0, 0.0)
        budget_a = max(0.0, effective_ccl_a + load_allowance_a - self.config.reserve_a)

        if battery_current_a > effective_ccl_a + self.config.feedback_tolerance_a:
            excess_a = battery_current_a - effective_ccl_a
            budget_a = max(0.0, budget_a - excess_a)
            reason = "feedback_clamp"

        targets, weight_basis = self._allocate_budget(chargers, budget_a, reason)
        return ChargeAllocationDecision(
            budget_a=round(budget_a, 1),
            bms_ccl_a=bms_ccl_a,
            load_allowance_a=round(load_allowance_a, 1),
            battery_current_a=round(battery_current_a, 1),
            battery_charge_a=max(battery_current_a, 0.0),
            reason=reason,
            targets=targets,
            weight_basis=weight_basis,
            charge_ceiling_a=charge_ceiling_a,
        )

    def _allocate_budget(
        self,
        chargers: list[ChargerAllocationInput],
        budget_a: float,
        reason: str,
    ) -> tuple[dict[str, ChargerAllocationTarget], str | None]:
        targets: dict[str, ChargerAllocationTarget] = {}
        eligible = [
            charger
            for charger in chargers
            if charger.online and charger.enabled and charger.active and charger.max_current_a > 0
        ]
        eligible_names = {charger.name for charger in eligible}

        weights, weight_basis = self._weights(eligible)
        caps = {charger.name: charger.max_current_a for charger in eligible}
        raw_allocations = _waterfill_allocate(budget_a, weights, caps)
        allocations = _whole_amp_allocations(
            raw_allocations,
            budget_a,
            caps,
        )

        for charger in chargers:
            if not charger.online:
                targets[charger.name] = ChargerAllocationTarget(None, False, "charger offline")
                continue
            if not charger.enabled:
                targets[charger.name] = self._target(charger, 0.0, "charger disabled")
                continue
            if not charger.active:
                targets[charger.name] = self._release_target(charger, "charger inactive")
                continue
            if charger.name not in eligible_names:
                targets[charger.name] = self._release_target(charger, "charger unavailable")
                continue
            # Eligible apportionment: never disable here -- a producing charger
            # that wins only a sub-floor share keeps charging at min_current.
            targets[charger.name] = self._target(
                charger, allocations.get(charger.name, 0.0), reason, allow_disable=False
            )
        return targets, weight_basis

    def _weights(
        self, eligible: list[ChargerAllocationInput]
    ) -> tuple[dict[str, float], str | None]:
        """Apportionment weights and the basis used.

        Split constrained charge budget evenly across eligible chargers. This is
        intentionally simple: controller PV/input power can itself collapse when
        a prior limit holds the controller near zero, so using it as the weight
        can latch a charger out of the allocation.
        """
        if not eligible:
            return {}, None
        return ({charger.name: 1.0 for charger in eligible}, "equal")

    def _target(
        self,
        charger: ChargerAllocationInput,
        target_current_a: float,
        reason: str,
        *,
        allow_disable: bool = True,
    ) -> ChargerAllocationTarget:
        target = _whole_amp_limit(target_current_a, charger.max_current_a)
        # Only a genuine stop (resolved allowance is 0 A -- the callers that pass
        # allow_disable) commands the charger OFF. During
        # apportionment an eligible, producing charger that merely loses the split
        # keeps charging at its floor instead of being switched off -- otherwise a
        # controller can get its coil flapped on/off by ordinary split changes.
        if allow_disable and (target <= 0.0 or target < charger.min_current_a):
            # write off if the charger might still be on (or its state is unknown);
            # the actuator is idempotent if already off.
            should_write = charger.current_limit_a is None or charger.current_limit_a > 0.0
            return ChargerAllocationTarget(0.0, should_write, reason, True)
        if target < charger.min_current_a:
            target = charger.min_current_a
        should_write = (
            charger.current_limit_a is not None
            and abs(charger.current_limit_a - target) >= self.config.min_write_delta_a
        )
        return ChargerAllocationTarget(target, should_write, reason, False)

    def _release_target(self, charger: ChargerAllocationInput, reason: str) -> ChargerAllocationTarget:
        """Release stale allocation constraints when a charger is not a usable
        resource right now.

        No-PV / sleeping-controller states are not battery safety events. Return
        the current limit toward the device's normal ceiling so it is ready for
        the next wakeup, but never request an EPEver coil disable from this path.
        """
        target = _whole_amp_limit(charger.max_current_a, charger.max_current_a)
        should_write = (
            charger.current_limit_a is not None
            and abs(charger.current_limit_a - target) >= self.config.min_write_delta_a
        )
        return ChargerAllocationTarget(target, should_write, reason, False)

    def _zero_targets(
        self,
        reason: str,
        bms_ccl_a: float | None,
        battery_current_a: float | None,
        load_allowance_a: float,
        chargers: list[ChargerAllocationInput],
        charge_ceiling_a: float | None = None,
    ) -> ChargeAllocationDecision:
        return ChargeAllocationDecision(
            budget_a=0.0,
            bms_ccl_a=bms_ccl_a,
            load_allowance_a=round(load_allowance_a, 1),
            battery_current_a=None if battery_current_a is None else round(battery_current_a, 1),
            battery_charge_a=None if battery_current_a is None else max(battery_current_a, 0.0),
            reason=reason,
            targets={
                charger.name: self._target(charger, 0.0, reason)
                if charger.online
                else ChargerAllocationTarget(None, False, "charger offline")
                for charger in chargers
            },
            charge_ceiling_a=charge_ceiling_a,
        )

    def _no_targets(
        self,
        reason: str,
        bms_ccl_a: float | None,
        battery_current_a: float | None,
        chargers: list[ChargerAllocationInput],
    ) -> ChargeAllocationDecision:
        return ChargeAllocationDecision(
            budget_a=None,
            bms_ccl_a=bms_ccl_a,
            load_allowance_a=0.0,
            battery_current_a=None if battery_current_a is None else round(battery_current_a, 1),
            battery_charge_a=None if battery_current_a is None else max(battery_current_a, 0.0),
            reason=reason,
            targets={charger.name: ChargerAllocationTarget(None, False, reason) for charger in chargers},
        )


def allocation_detail(
    decision: ChargeAllocationDecision,
    *,
    dry_run: bool,
    ccl_scaling_factor: float | None = None,
) -> dict:
    """Compact, serializable view of a decision -- shared by the telemetry event
    and the snapshot API/display so they never drift.

    ``ccl_scaling_factor`` is the live operator knob from the ChargeCeiling; it
    is surfaced here so the display can show and tune it alongside the allocation.
    """
    return {
        "mode": "dry-run" if dry_run else "live",
        "reason": decision.reason,
        "ccl_scaling_factor": ccl_scaling_factor,
        "bms_ccl_a": decision.bms_ccl_a,
        "allowance_a": decision.charge_ceiling_a,
        "charge_ceiling_a": decision.charge_ceiling_a,
        "budget_a": decision.budget_a,
        "load_allowance_a": decision.load_allowance_a,
        "battery_current_a": decision.battery_current_a,
        "battery_charge_a": decision.battery_charge_a,
        "weight_basis": decision.weight_basis,
        "targets": {
            name: {
                "target_a": target.target_current_a,
                "disable": target.disable,
                "should_write": target.should_write,
                "reason": target.reason,
            }
            for name, target in decision.targets.items()
        },
    }


def charge_allocation_event(
    decision: ChargeAllocationDecision,
    *,
    dry_run: bool,
    captured_at: datetime | None = None,
) -> TelemetryEvent:
    """Durable event for one allocation decision.

    Dry-run records must survive reboots (journald may be volatile on the Pi),
    and these traces are how we tune reserve/deadband/weights from real days.
    """
    return TelemetryEvent(
        captured_at=captured_at or datetime.now(timezone.utc),
        source="charge_allocator",
        event="allocation_decision",
        detail=allocation_detail(decision, dry_run=dry_run),
    )


def charge_limit_write_event(
    *,
    controller: str,
    target_a: float,
    previous_a: float | None,
    reason: str,
    disable: bool,
    success: bool,
    error: str | None = None,
    captured_at: datetime | None = None,
) -> TelemetryEvent:
    detail = {
        "controller": controller,
        "action": "current_limit",
        "target_a": target_a,
        "previous_a": previous_a,
        "reason": reason,
        "disable": disable,
        "success": success,
    }
    if error:
        detail["error"] = error
    return TelemetryEvent(
        captured_at=captured_at or datetime.now(timezone.utc),
        source="charge_allocator",
        event="limit_write",
        detail=detail,
    )


def charge_enable_write_event(
    *,
    controller: str,
    enabled: bool,
    previous_enabled: bool | None,
    reason: str,
    success: bool,
    error: str | None = None,
    captured_at: datetime | None = None,
) -> TelemetryEvent:
    detail = {
        "controller": controller,
        "action": "charge_enable",
        "enabled": enabled,
        "previous_enabled": previous_enabled,
        "reason": reason,
        "success": success,
    }
    if error:
        detail["error"] = error
    return TelemetryEvent(
        captured_at=captured_at or datetime.now(timezone.utc),
        source="charge_allocator",
        event="charge_enable_write",
        detail=detail,
    )


def _waterfill_allocate(
    budget_a: float,
    weights: dict[str, float],
    caps: dict[str, float],
) -> dict[str, float]:
    remaining_budget = max(0.0, budget_a)
    remaining = {name for name, weight in weights.items() if weight > 0 and caps.get(name, 0.0) > 0}
    allocations = {name: 0.0 for name in weights}

    while remaining and remaining_budget > 0:
        total_weight = sum(weights[name] for name in remaining)
        if total_weight <= 0:
            break
        progressed = False
        for name in list(remaining):
            share = remaining_budget * weights[name] / total_weight
            cap_left = caps[name] - allocations[name]
            if share >= cap_left:
                allocations[name] += cap_left
                remaining_budget -= cap_left
                remaining.remove(name)
                progressed = True
        if not progressed:
            for name in remaining:
                allocations[name] += remaining_budget * weights[name] / total_weight
            remaining_budget = 0.0

    return allocations


def _whole_amp_limit(target_current_a: float, max_current_a: float) -> float:
    bounded = max(0.0, min(target_current_a, max_current_a))
    return float(math.floor(bounded + 1e-9))


def _whole_amp_allocations(
    allocations: dict[str, float],
    budget_a: float,
    caps: dict[str, float],
) -> dict[str, float]:
    whole_budget = int(math.floor(max(0.0, budget_a) + 1e-9))
    cap_floor = {name: int(math.floor(max(0.0, caps.get(name, 0.0)) + 1e-9)) for name in allocations}
    whole = {
        name: min(int(math.floor(max(0.0, current) + 1e-9)), cap_floor[name])
        for name, current in allocations.items()
    }
    leftover = max(0, whole_budget - sum(whole.values()))
    candidates = sorted(
        allocations,
        key=lambda name: (-(max(0.0, allocations[name]) - math.floor(max(0.0, allocations[name]))), name),
    )
    while leftover > 0:
        progressed = False
        for name in candidates:
            if leftover <= 0:
                break
            if whole[name] >= cap_floor[name]:
                continue
            whole[name] += 1
            leftover -= 1
            progressed = True
        if not progressed:
            break
    return {name: float(current) for name, current in whole.items()}
