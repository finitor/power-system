"""System-level allocation of BMS charge-current headroom across chargers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .metrics import TelemetryEvent


@dataclass(frozen=True)
class ChargeAllocatorConfig:
    reserve_a: float = 5.0
    feedback_tolerance_a: float = 1.0
    # Sunrise floors so a not-yet-producing charger keeps a share of budget. One
    # floor per weight basis (watts for PV-power weighting, amps for the output
    # fallback) -- see ChargeCurrentAllocator._weights.
    min_active_weight_a: float = 1.0
    min_active_weight_w: float = 10.0
    min_write_delta_a: float = 2.0


@dataclass(frozen=True)
class ChargerAllocationInput:
    name: str
    actual_current_a: float
    current_limit_a: float | None
    max_current_a: float
    # PV input power is the preferred apportionment weight: it reflects how much
    # the array *could* deliver, independent of the limit we last wrote. Weighting
    # by throttled output latches an allocation (throttle -> low output -> low
    # weight -> stays throttled). None falls back to actual output.
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
    # True when the charger should be turned off (target below its representable
    # floor, or zero) rather than current-limited. The actuator layer maps this
    # to the EPEver charge coil off / a 0 A Classic limit.
    disable: bool = False


@dataclass(frozen=True)
class ChargeAllocationDecision:
    budget_a: float | None
    bms_ccl_a: float | None
    load_allowance_a: float
    battery_charge_a: float | None
    reason: str
    targets: dict[str, ChargerAllocationTarget]
    # "pv_power" or "actual_current": which signal drove the apportionment split,
    # for trace analysis. None when no budget was apportioned.
    weight_basis: str | None = None
    # Battery-state ceiling on net charge current (top-knee taper / cell safety);
    # combined with bms_ccl_a via min(). None = no such constraint this cycle.
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

        if not charge_enabled:
            return self._zero_targets(
                "BMS charge disabled",
                bms_ccl_a,
                battery_current_a,
                max(load_current_a or 0.0, 0.0),
                chargers,
                charge_ceiling_a=charge_ceiling_a,
            )
        if bms_ccl_a <= 0:
            return self._zero_targets(
                "BMS CCL is zero",
                bms_ccl_a,
                battery_current_a,
                max(load_current_a or 0.0, 0.0),
                chargers,
                charge_ceiling_a=charge_ceiling_a,
            )

        # The battery-state ceiling (top-knee taper / cell safety) is a second
        # limit on net battery charge current; the binding one wins.
        effective_ccl_a = bms_ccl_a
        reason = "normal_load_allowance"
        if charge_ceiling_a is not None and charge_ceiling_a < bms_ccl_a:
            effective_ccl_a = max(0.0, charge_ceiling_a)
            reason = charge_ceiling_reason or "charge_ceiling"
            if effective_ccl_a <= 0.0:
                return self._zero_targets(
                    reason,
                    bms_ccl_a,
                    battery_current_a,
                    max(load_current_a or 0.0, 0.0),
                    chargers,
                    charge_ceiling_a=charge_ceiling_a,
                )

        # If every eligible charger at full output still couldn't reach the
        # battery limit, the allocator is not the binding constraint (sunlight
        # is). Impose nothing: pin each to its own max, don't apportion, don't
        # subtract reserve. This avoids needlessly throttling -- and needlessly
        # writing -- through the abundant-headroom part of the day.
        eligible_max_a = sum(
            charger.max_current_a
            for charger in chargers
            if charger.online and charger.enabled and charger.active and charger.max_current_a > 0
        )
        if eligible_max_a <= effective_ccl_a:
            targets, weight_basis = self._allocate_budget(chargers, eligible_max_a, "unconstrained")
            return ChargeAllocationDecision(
                budget_a=round(eligible_max_a, 1),
                bms_ccl_a=bms_ccl_a,
                load_allowance_a=round(max(load_current_a or 0.0, 0.0), 1),
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
        allocations = _waterfill_allocate(
            budget_a,
            weights,
            {charger.name: charger.max_current_a for charger in eligible},
        )

        for charger in chargers:
            if not charger.online:
                targets[charger.name] = ChargerAllocationTarget(None, False, "charger offline")
                continue
            if not charger.enabled:
                targets[charger.name] = self._target(charger, 0.0, "charger disabled")
                continue
            if not charger.active:
                targets[charger.name] = self._target(charger, 0.0, "charger inactive")
                continue
            if charger.name not in eligible_names:
                targets[charger.name] = self._target(charger, 0.0, "charger unavailable")
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

        Prefer PV input power for *every* eligible charger -- a resource signal
        independent of the limit we wrote. If any eligible charger lacks it, fall
        back to actual output for all of them so the basis stays consistent;
        mixing watts and amps would corrupt the proportional ratios.
        """
        if not eligible:
            return {}, None
        if all(charger.pv_power_w is not None for charger in eligible):
            return (
                {
                    charger.name: max(charger.pv_power_w, self.config.min_active_weight_w)
                    for charger in eligible
                },
                "pv_power",
            )
        return (
            {
                charger.name: max(charger.actual_current_a, self.config.min_active_weight_a)
                for charger in eligible
            },
            "actual_current",
        )

    def _target(
        self,
        charger: ChargerAllocationInput,
        target_current_a: float,
        reason: str,
        *,
        allow_disable: bool = True,
    ) -> ChargerAllocationTarget:
        target = round(max(0.0, min(target_current_a, charger.max_current_a)), 1)
        # Only a genuine stop (charge disabled, CCL/ceiling <= 0, latch, safety --
        # the callers that pass allow_disable) commands the charger OFF. During
        # apportionment an eligible, producing charger that merely loses the split
        # keeps charging at its floor instead of being switched off -- otherwise a
        # controller whose PV-power weight dips gets its coil flapped on/off.
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
            battery_charge_a=None if battery_current_a is None else max(battery_current_a, 0.0),
            reason=reason,
            targets={charger.name: ChargerAllocationTarget(None, False, reason) for charger in chargers},
        )


def allocation_detail(decision: ChargeAllocationDecision, *, dry_run: bool) -> dict:
    """Compact, serializable view of a decision -- shared by the telemetry event
    and the snapshot API/display so they never drift."""
    return {
        "mode": "dry-run" if dry_run else "live",
        "reason": decision.reason,
        "bms_ccl_a": decision.bms_ccl_a,
        "charge_ceiling_a": decision.charge_ceiling_a,
        "budget_a": decision.budget_a,
        "load_allowance_a": decision.load_allowance_a,
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

    return {name: round(current, 1) for name, current in allocations.items()}
