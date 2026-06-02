"""Safety checks for charger settings against BMS-advertised limits."""

from __future__ import annotations

from dataclasses import dataclass

from .canbus import PylonChargeLimits
from .classic import ClassicChargeSettings


@dataclass(frozen=True)
class ClassicChargeTargets:
    battery_current_limit_a: float | None = None
    absorb_voltage_v: float | None = None
    float_voltage_v: float | None = None
    equalize_voltage_v: float | None = None
    absorb_time_s: int | None = None
    max_temp_comp_voltage_v: float | None = None


def planned_classic_settings(
    current: ClassicChargeSettings,
    targets: ClassicChargeTargets,
) -> ClassicChargeTargets:
    return ClassicChargeTargets(
        battery_current_limit_a=_target_or_current(targets.battery_current_limit_a, current.battery_current_limit_a),
        absorb_voltage_v=_target_or_current(targets.absorb_voltage_v, current.absorb_voltage_v),
        float_voltage_v=_target_or_current(targets.float_voltage_v, current.float_voltage_v),
        equalize_voltage_v=_target_or_current(targets.equalize_voltage_v, current.equalize_voltage_v),
        absorb_time_s=targets.absorb_time_s if targets.absorb_time_s is not None else current.absorb_time_s,
        max_temp_comp_voltage_v=_target_or_current(targets.max_temp_comp_voltage_v, current.max_temp_comp_voltage_v),
    )


def validate_classic_targets_against_bms(
    planned: ClassicChargeTargets,
    charge_limits: PylonChargeLimits,
) -> list[str]:
    violations: list[str] = []
    voltage_limit = charge_limits.charge_voltage_limit_v
    current_limit = charge_limits.charge_current_limit_a

    for label, value in (
        ("Absorb voltage", planned.absorb_voltage_v),
        ("Float voltage", planned.float_voltage_v),
        ("Equalize voltage", planned.equalize_voltage_v),
        ("Max temp-comp voltage", planned.max_temp_comp_voltage_v),
    ):
        if value is not None and value > voltage_limit:
            violations.append(f"{label} {value:.1f}V exceeds BMS CVL {voltage_limit:.1f}V")

    if planned.battery_current_limit_a is not None and planned.battery_current_limit_a > current_limit:
        violations.append(
            f"Battery current limit {planned.battery_current_limit_a:.1f}A exceeds BMS CCL {current_limit:.1f}A"
        )
    return violations


def _target_or_current(target: float | None, current: float) -> float:
    return current if target is None else target
