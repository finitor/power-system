"""Supervisor-driven relay control: heater/fan and Classic charge-disable."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .charge_allocator import AllocationOverride, ChargeAllocationDecision
    from .relay import RelayController
    from .supervisor import SupervisorSnapshot

log = logging.getLogger(__name__)

# Relay 1 (heat_fan) thresholds
# DRY RUN values — revert to 2.0/5.0/134.0/130.0 before live use
_HEAT_ON_TEMP_C = 17.5     # activate below this minimum cell temperature
_HEAT_OFF_TEMP_C = 20.0    # deactivate above this minimum cell temperature
_HEAT_ON_VOC_V = 134.0     # activate above this Classic VOC
_HEAT_OFF_VOC_V = 130.0    # deactivate below this Classic VOC


class RelaySupervisor:
    """Evaluates relay states each supervisor tick and drives the RelayController.

    Relay 1 (heat_fan): heater + fan, hysteresis control.
      On  when min cell temp < 2 °C AND Classic VOC > 134 V
      Off when min cell temp > 5 °C OR  Classic VOC < 130 V

    Relay 2 (charge_disable): activates whenever Classic is commanded to 0 A,
      via the allocator (disable flag or 0 A target) or a manual ceiling override.
      Applies >6 V to Classic AUX2+ (Active HIGH input turn off, function 15).
    """

    def __init__(self, relay_controller: RelayController) -> None:
        self._relay = relay_controller
        self._heat_fan_on: bool = False

    def update(
        self,
        snapshot: SupervisorSnapshot,
        allocation_decision: ChargeAllocationDecision | None,
        allocation_override: AllocationOverride | None = None,
    ) -> None:
        self._update_heat_fan(snapshot)
        self._update_charge_disable(allocation_decision, allocation_override)

    def _update_heat_fan(self, snapshot: SupervisorSnapshot) -> None:
        temp_c = _pack_temp(snapshot)
        voc_v = snapshot.classic.last_voc_v if snapshot.classic is not None else None

        if temp_c is None or voc_v is None:
            return

        if self._heat_fan_on:
            want = not (temp_c > _HEAT_OFF_TEMP_C or voc_v < _HEAT_OFF_VOC_V)
        else:
            want = temp_c < _HEAT_ON_TEMP_C and voc_v > _HEAT_ON_VOC_V

        if want != self._heat_fan_on:
            log.info(
                "relay heat_fan %s -> %s (temp=%.1f°C voc=%.1fV)",
                "on" if self._heat_fan_on else "off",
                "on" if want else "off",
                temp_c,
                voc_v,
            )
            try:
                self._relay.set("heat_fan", want)
                self._heat_fan_on = want
            except Exception as exc:  # noqa: BLE001
                log.error("relay heat_fan set failed: %s", exc)

    def _update_charge_disable(
        self,
        allocation_decision: ChargeAllocationDecision | None,
        allocation_override: AllocationOverride | None,
    ) -> None:
        if allocation_decision is None:
            return
        # Apply override (manual ceilings) to get the effective targets — the
        # same view that _apply() uses when writing to the Classic.
        targets = (
            allocation_override.apply(allocation_decision.targets)
            if allocation_override is not None
            else allocation_decision.targets
        )
        classic_target = targets.get("classic")
        if classic_target is None:
            return
        want = classic_target.disable or classic_target.target_current_a == 0.0
        current = self._relay.state()["charge_disable"]
        if want != current:
            log.info(
                "relay charge_disable %s -> %s (reason=%s)",
                "on" if current else "off",
                "on" if want else "off",
                classic_target.reason,
            )
            try:
                self._relay.set("charge_disable", want)
            except Exception as exc:  # noqa: BLE001
                log.error("relay charge_disable set failed: %s", exc)


def _pack_temp(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.battery is None:
        return None
    return snapshot.battery.min_cell_temperature_c
