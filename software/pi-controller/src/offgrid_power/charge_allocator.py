"""System-level allocation of BMS charge-current headroom across chargers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChargeAllocatorConfig:
    reserve_a: float = 5.0
    feedback_tolerance_a: float = 1.0
    min_active_weight_a: float = 1.0
    min_write_delta_a: float = 2.0


@dataclass(frozen=True)
class ChargerAllocationInput:
    name: str
    actual_current_a: float
    current_limit_a: float | None
    max_current_a: float
    online: bool = True
    enabled: bool = True
    active: bool = True


@dataclass(frozen=True)
class ChargerAllocationTarget:
    target_current_a: float | None
    should_write: bool
    reason: str


@dataclass(frozen=True)
class ChargeAllocationDecision:
    budget_a: float | None
    bms_ccl_a: float | None
    load_allowance_a: float
    battery_charge_a: float | None
    reason: str
    targets: dict[str, ChargerAllocationTarget]


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
            )
        if bms_ccl_a <= 0:
            return self._zero_targets(
                "BMS CCL is zero",
                bms_ccl_a,
                battery_current_a,
                max(load_current_a or 0.0, 0.0),
                chargers,
            )

        load_allowance_a = max(load_current_a or 0.0, 0.0)
        budget_a = max(0.0, bms_ccl_a + load_allowance_a - self.config.reserve_a)
        reason = "normal_load_allowance"

        if battery_current_a > bms_ccl_a + self.config.feedback_tolerance_a:
            excess_a = battery_current_a - bms_ccl_a
            budget_a = max(0.0, budget_a - excess_a)
            reason = "feedback_clamp"

        targets = self._allocate_budget(chargers, budget_a, reason)
        return ChargeAllocationDecision(
            budget_a=round(budget_a, 1),
            bms_ccl_a=bms_ccl_a,
            load_allowance_a=round(load_allowance_a, 1),
            battery_charge_a=max(battery_current_a, 0.0),
            reason=reason,
            targets=targets,
        )

    def _allocate_budget(
        self,
        chargers: list[ChargerAllocationInput],
        budget_a: float,
        reason: str,
    ) -> dict[str, ChargerAllocationTarget]:
        targets: dict[str, ChargerAllocationTarget] = {}
        eligible = [
            charger
            for charger in chargers
            if charger.online and charger.enabled and charger.active and charger.max_current_a > 0
        ]
        eligible_names = {charger.name for charger in eligible}

        allocations = _waterfill_allocate(
            budget_a,
            {
                charger.name: max(charger.actual_current_a, self.config.min_active_weight_a)
                for charger in eligible
            },
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
            targets[charger.name] = self._target(charger, allocations.get(charger.name, 0.0), reason)
        return targets

    def _target(
        self,
        charger: ChargerAllocationInput,
        target_current_a: float,
        reason: str,
    ) -> ChargerAllocationTarget:
        target = round(max(0.0, min(target_current_a, charger.max_current_a)), 1)
        should_write = (
            charger.current_limit_a is not None
            and abs(charger.current_limit_a - target) >= self.config.min_write_delta_a
        )
        return ChargerAllocationTarget(target, should_write, reason)

    def _zero_targets(
        self,
        reason: str,
        bms_ccl_a: float | None,
        battery_current_a: float | None,
        load_allowance_a: float,
        chargers: list[ChargerAllocationInput],
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
