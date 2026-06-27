"""Supervisor-driven relay control: heater/fan and Classic charge-disable."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING

from .metrics import TelemetryEvent

if TYPE_CHECKING:
    from .charge_allocator import AllocationOverride, ChargeAllocationDecision
    from .relay import RelayController
    from .supervisor import SupervisorSnapshot

log = logging.getLogger(__name__)


def heat_fan_transition_event(
    *,
    active: bool,
    temp_c: float,
    voc_v: float,
    max_temp_c: float | None = None,
    captured_at: datetime | None = None,
) -> TelemetryEvent:
    detail: dict = {"active": active, "temp_c": temp_c, "voc_v": voc_v}
    if max_temp_c is not None:
        detail["max_temp_c"] = max_temp_c
    return TelemetryEvent(
        captured_at=captured_at or datetime.now(timezone.utc),
        source="relay",
        event="heat_fan_transition",
        detail=detail,
    )

# Relay 1 (heat_fan) — reactive mode thresholds
# DRY RUN values — revert to 2.0/5.0 before live use
_HEAT_ON_TEMP_C = 17.5     # activate below this minimum cell temperature
_HEAT_OFF_TEMP_C = 20.0    # deactivate above this minimum cell temperature
_HEAT_ON_VOC_V = 132.0     # activate above this Classic VOC
_HEAT_OFF_VOC_V = 130.0    # deactivate below this Classic VOC

# Relay 1 (heat_fan) — preventive pre-warm mode thresholds
_PREVENT_AMBIENT_ON_C = 5.0    # enable pre-warming below this ambient temperature
_PREVENT_PACK_OFF_C = 25.0     # stop pre-warming once pack reaches this temperature
_PREVENT_SOC_ON_PCT = 95       # enable pre-warming above this battery SOC

# Hard cutout shared by both modes: turn off if any cell reaches this temperature.
_HEAT_MAX_CELL_CUTOUT_C = _PREVENT_PACK_OFF_C


class RelaySupervisor:
    """Evaluates relay states each supervisor tick and drives the RelayController.

    Relay 1 (heat_fan): two independent control modes, OR'd together.

      Reactive mode — protects pack from charge-inhibit due to cold:
        On  when min cell temp < 2 °C AND Classic VOC > 132 V
        Off when min cell temp > 5 °C OR  Classic VOC < 130 V

      Preventive mode — pre-warms pack using surplus solar energy:
        On  when ambient < 5 °C AND pack < 25 °C AND SOC > 95 %
        Off when ambient ≥ 5 °C OR  pack ≥ 25 °C OR SOC ≤ 95 %

    Relay 2 (charge_disable): activates whenever Classic is commanded to 0 A,
      via the allocator (disable flag or 0 A target) or a manual ceiling override.
      Applies >6 V to Classic AUX2+ (Active HIGH input turn off, function 15).
    """

    def __init__(
        self,
        relay_controller: RelayController,
        ambient_temp_fn: Callable[[SupervisorSnapshot], float | None] | None = None,
    ) -> None:
        self._relay = relay_controller
        self._ambient_temp = ambient_temp_fn or _snapshot_ambient_temp
        self._heat_fan_on: bool = False
        self._reactive_on: bool = False    # reactive-mode hysteresis state
        self._preventive_on: bool = False  # preventive-mode hysteresis state

    @property
    def heat_fan_on(self) -> bool:
        return self._heat_fan_on

    def update(
        self,
        snapshot: SupervisorSnapshot,
        allocation_decision: ChargeAllocationDecision | None,
        allocation_override: AllocationOverride | None = None,
    ) -> None:
        self._update_heat_fan(snapshot)
        self._update_charge_disable(allocation_decision, allocation_override)

    def _update_heat_fan(self, snapshot: SupervisorSnapshot) -> None:
        reactive_want = self._reactive_heat_want(snapshot)
        preventive_want = self._preventive_heat_want(snapshot)
        want = reactive_want or preventive_want

        # Hard cutout: any cell too warm shuts off both modes and resets their
        # hysteresis state so they re-evaluate from OFF when the cutout clears.
        max_temp_c = _pack_max_temp(snapshot)
        if max_temp_c is not None and max_temp_c >= _HEAT_MAX_CELL_CUTOUT_C:
            want = False
            reactive_want = False
            preventive_want = False

        self._reactive_on = reactive_want
        self._preventive_on = preventive_want

        if want != self._heat_fan_on:
            active_modes = []
            if reactive_want:
                active_modes.append("reactive")
            if preventive_want:
                active_modes.append("preventive")
            mode_str = "+".join(active_modes) if active_modes else "off"
            temp_c = _pack_temp(snapshot)
            max_temp_c = _pack_max_temp(snapshot)
            voc_v = snapshot.classic.last_voc_v if snapshot.classic is not None else None
            log.info(
                "relay heat_fan %s -> %s (mode=%s min=%s max=%s voc=%s)",
                "on" if self._heat_fan_on else "off",
                "on" if want else "off",
                mode_str,
                f"{temp_c:.1f}°C" if temp_c is not None else "?",
                f"{max_temp_c:.1f}°C" if max_temp_c is not None else "?",
                f"{voc_v:.1f}V" if voc_v is not None else "?",
            )
            try:
                self._relay.set("heat_fan", want)
                self._heat_fan_on = want
            except Exception as exc:  # noqa: BLE001
                log.error("relay heat_fan set failed: %s", exc)

    def _reactive_heat_want(self, snapshot: SupervisorSnapshot) -> bool:
        temp_c = _pack_temp(snapshot)
        voc_v = snapshot.classic.last_voc_v if snapshot.classic is not None else None
        if temp_c is None or voc_v is None:
            return self._reactive_on  # hold current state when data unavailable
        if self._reactive_on:
            return not (temp_c > _HEAT_OFF_TEMP_C or voc_v < _HEAT_OFF_VOC_V)
        return temp_c < _HEAT_ON_TEMP_C and voc_v > _HEAT_ON_VOC_V

    def _preventive_heat_want(self, snapshot: SupervisorSnapshot) -> bool:
        ambient_c = self._ambient_temp(snapshot)
        battery = snapshot.battery
        if ambient_c is None or battery is None:
            return False
        soc = battery.state_of_charge
        pack_c = _pack_temp(snapshot)
        if soc is None or pack_c is None:
            return False
        if self._preventive_on:
            return not (
                ambient_c >= _PREVENT_AMBIENT_ON_C
                or pack_c >= _PREVENT_PACK_OFF_C
                or soc.soc_percent <= _PREVENT_SOC_ON_PCT
            )
        return (
            ambient_c < _PREVENT_AMBIENT_ON_C
            and pack_c < _PREVENT_PACK_OFF_C
            and soc.soc_percent > _PREVENT_SOC_ON_PCT
        )

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


def _pack_max_temp(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.battery is None:
        return None
    return snapshot.battery.max_cell_temperature_c


def _snapshot_ambient_temp(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.ambient is None:
        return None
    return snapshot.ambient.temperature_c
