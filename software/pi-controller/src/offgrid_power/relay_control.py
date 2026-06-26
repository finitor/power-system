"""Supervisor-driven relay control: heater/fan and Classic charge-disable."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .charge_allocator import ChargeAllocationDecision
    from .relay import RelayController
    from .supervisor import SupervisorSnapshot

log = logging.getLogger(__name__)

# Relay 1 (heat_fan) thresholds
_HEAT_ON_TEMP_C = 0.0      # activate below this pack temperature
_HEAT_OFF_TEMP_C = 5.0     # deactivate above this pack temperature
_HEAT_ON_VOC_V = 134.0     # activate above this Classic VOC
_HEAT_OFF_VOC_V = 130.0    # deactivate below this Classic VOC


class RelaySupervisor:
    """Evaluates relay states each supervisor tick and drives the RelayController.

    Relay 1 (heat_fan): heater + fan, hysteresis control.
      On  when pack temp < 0 °C AND Classic VOC > 134 V
      Off when pack temp > 5 °C OR  Classic VOC < 130 V

    Relay 2 (charge_disable): mirrors the Classic allocator hard-disable.
      On  when the CCL allocator sets Classic to 0 A (disable=True)
      Off otherwise
    """

    def __init__(self, relay_controller: RelayController) -> None:
        self._relay = relay_controller
        self._heat_fan_on: bool = False

    def update(
        self,
        snapshot: SupervisorSnapshot,
        allocation_decision: ChargeAllocationDecision | None,
    ) -> None:
        self._update_heat_fan(snapshot)
        self._update_charge_disable(allocation_decision)

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
        self, allocation_decision: ChargeAllocationDecision | None
    ) -> None:
        if allocation_decision is None:
            return
        classic_target = allocation_decision.targets.get("classic")
        if classic_target is None:
            return
        want = classic_target.disable
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
    if snapshot.battery.measurements is None:
        return None
    return snapshot.battery.measurements.temperature_c
