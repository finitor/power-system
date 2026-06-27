from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from datetime import datetime, timezone

from offgrid_power.ambient import AmbientTelemetry
from offgrid_power.charge_allocator import AllocationOverride, ChargeAllocationDecision, ChargerAllocationTarget
from offgrid_power.relay_control import (
    RelaySupervisor,
    _HEAT_ON_TEMP_C, _HEAT_OFF_TEMP_C, _HEAT_ON_VOC_V, _HEAT_OFF_VOC_V,
    _HEAT_MAX_CELL_CUTOUT_C,
    _PREVENT_AMBIENT_ON_C, _PREVENT_PACK_OFF_C, _PREVENT_SOC_ON_PCT,
)
from snapshot_helpers import make_battery_snapshot, make_classic_telemetry, make_snapshot


class StubRelay:
    def __init__(self):
        self._state = {"heat_fan": False, "charge_disable": False}

    def set(self, name, on):
        self._state[name] = on

    def state(self):
        return dict(self._state)

    is_stub = True


def _snapshot(temp_c=10.0, voc_v=120.0, max_temp_c=None):
    battery = make_battery_snapshot(min_cell_temperature_c=temp_c, max_cell_temperature_c=max_temp_c)
    classic = make_classic_telemetry(last_voc_v=voc_v)
    return make_snapshot(battery=battery, classic=classic)


def _decision(classic_disable: bool, classic_reason: str = "test") -> ChargeAllocationDecision:
    return ChargeAllocationDecision(
        targets={
            "classic": ChargerAllocationTarget(
                target_current_a=0.0 if classic_disable else 20.0,
                should_write=True,
                reason=classic_reason,
                disable=classic_disable,
            )
        },
        reason=classic_reason,
        budget_a=0.0 if classic_disable else 20.0,
        bms_ccl_a=0.0 if classic_disable else 20.0,
        load_allowance_a=0.0,
        battery_current_a=None,
        battery_charge_a=None,
    )


_COLD = _HEAT_ON_TEMP_C - 2.0       # clearly below cut-in
_WARM = _HEAT_OFF_TEMP_C + 1.0      # clearly above cut-out
_MID = (_HEAT_ON_TEMP_C + _HEAT_OFF_TEMP_C) / 2  # inside hysteresis band
_VOC_HI = _HEAT_ON_VOC_V + 1.0     # above VOC cut-in
_VOC_MID = (_HEAT_ON_VOC_V + _HEAT_OFF_VOC_V) / 2  # inside VOC hysteresis
_VOC_LO = _HEAT_OFF_VOC_V - 1.0    # below VOC cut-out


class TestHeatFanHysteresis(unittest.TestCase):
    def setUp(self):
        self.relay = StubRelay()
        self.rs = RelaySupervisor(self.relay)

    def test_activates_when_cold_and_sunny(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI), None)
        self.assertTrue(self.relay.state()["heat_fan"])

    def test_no_activation_warm(self):
        self.rs.update(_snapshot(temp_c=_WARM, voc_v=_VOC_HI), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_no_activation_low_voc(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_LO), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_stays_on_within_hysteresis(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI), None)
        # VOC drops into hysteresis band — should stay on
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_MID), None)
        self.assertTrue(self.relay.state()["heat_fan"])

    def test_cuts_out_below_voc_floor(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI), None)
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_LO), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_cuts_out_when_warm(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI), None)
        self.rs.update(_snapshot(temp_c=_WARM, voc_v=_VOC_HI), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_does_not_reactivate_within_hysteresis(self):
        # Turn on, warm past cut-out, cool back into band — stays off
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI), None)
        self.rs.update(_snapshot(temp_c=_WARM, voc_v=_VOC_HI), None)
        self.rs.update(_snapshot(temp_c=_MID, voc_v=_VOC_HI), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_no_data_no_change(self):
        snapshot = make_snapshot(battery=None, classic=None)
        self.rs.update(snapshot, None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_max_cell_cutout_prevents_activation(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI, max_temp_c=_HEAT_MAX_CELL_CUTOUT_C), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_max_cell_cutout_turns_off_running_heater(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI), None)
        self.assertTrue(self.relay.state()["heat_fan"])
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI, max_temp_c=_HEAT_MAX_CELL_CUTOUT_C), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_max_cell_below_cutout_does_not_block_activation(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI, max_temp_c=_HEAT_MAX_CELL_CUTOUT_C - 1.0), None)
        self.assertTrue(self.relay.state()["heat_fan"])

    def test_max_cell_none_does_not_block_activation(self):
        self.rs.update(_snapshot(temp_c=_COLD, voc_v=_VOC_HI, max_temp_c=None), None)
        self.assertTrue(self.relay.state()["heat_fan"])


class TestChargeDisable(unittest.TestCase):
    def setUp(self):
        self.relay = StubRelay()
        self.rs = RelaySupervisor(self.relay)

    def test_activates_when_classic_disabled(self):
        self.rs.update(_snapshot(), _decision(classic_disable=True))
        self.assertTrue(self.relay.state()["charge_disable"])

    def test_off_when_classic_enabled(self):
        self.rs.update(_snapshot(), _decision(classic_disable=False))
        self.assertFalse(self.relay.state()["charge_disable"])

    def test_no_change_without_allocation(self):
        self.rs.update(_snapshot(), None)
        self.assertFalse(self.relay.state()["charge_disable"])

    def test_clears_when_classic_reenabled(self):
        self.rs.update(_snapshot(), _decision(classic_disable=True))
        self.rs.update(_snapshot(), _decision(classic_disable=False))
        self.assertFalse(self.relay.state()["charge_disable"])

    def test_activates_via_zero_target_current(self):
        # Allocator returns 0A target without disable=True (e.g. manual ceiling clamped to 0)
        decision = _decision(classic_disable=False)
        # Patch target_current_a to 0.0 without disable
        target = decision.targets["classic"]
        patched = ChargerAllocationTarget(
            target_current_a=0.0,
            should_write=True,
            reason="manual_limit(0A)",
            disable=False,
        )
        decision = ChargeAllocationDecision(
            targets={"classic": patched},
            reason=decision.reason,
            budget_a=decision.budget_a,
            bms_ccl_a=decision.bms_ccl_a,
            load_allowance_a=decision.load_allowance_a,
            battery_current_a=decision.battery_current_a,
            battery_charge_a=decision.battery_charge_a,
        )
        self.rs.update(_snapshot(), decision)
        self.assertTrue(self.relay.state()["charge_disable"])

    def test_activates_via_manual_ceiling_zero(self):
        # AllocationOverride with 0A ceiling for Classic (index 0) clamps the target
        override = AllocationOverride()
        override.set_manual_limit(0, 0.0)
        # Base decision has non-zero target (disable=False)
        self.rs.update(_snapshot(), _decision(classic_disable=False), allocation_override=override)
        self.assertTrue(self.relay.state()["charge_disable"])

    def test_non_zero_manual_ceiling_does_not_activate(self):
        override = AllocationOverride()
        override.set_manual_limit(0, 10.0)
        self.rs.update(_snapshot(), _decision(classic_disable=False), allocation_override=override)
        self.assertFalse(self.relay.state()["charge_disable"])


def _ambient_fn(temp_c: float | None):
    """Returns an ambient_temp_fn that always returns the given temperature."""
    return lambda _snapshot: temp_c


def _snapshot_preventive(
    *,
    pack_c: float = 10.0,
    soc_pct: int = 98,
    ambient_c: float = 2.0,
):
    battery = make_battery_snapshot(min_cell_temperature_c=pack_c, soc_percent=soc_pct)
    return make_snapshot(battery=battery)


_COLD_AMBIENT = _PREVENT_AMBIENT_ON_C - 2.0   # clearly below ambient cut-in
_WARM_AMBIENT = _PREVENT_AMBIENT_ON_C + 1.0   # clearly above ambient cut-in
_COOL_PACK = _PREVENT_PACK_OFF_C - 5.0        # clearly below pack cutoff
_HOT_PACK = _PREVENT_PACK_OFF_C + 1.0         # clearly above pack cutoff
_SOC_HI = _PREVENT_SOC_ON_PCT + 2             # surplus SOC
_SOC_LO = _PREVENT_SOC_ON_PCT - 2             # insufficient SOC


class TestPreventiveHeatFan(unittest.TestCase):
    def _rs(self, ambient_c: float | None = _COLD_AMBIENT) -> tuple[StubRelay, RelaySupervisor]:
        relay = StubRelay()
        rs = RelaySupervisor(relay, ambient_temp_fn=_ambient_fn(ambient_c))
        return relay, rs

    def test_activates_when_cold_ambient_cool_pack_high_soc(self):
        relay, rs = self._rs()
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_HI), None)
        self.assertTrue(relay.state()["heat_fan"])

    def test_no_activation_when_ambient_warm(self):
        relay, rs = self._rs(ambient_c=_WARM_AMBIENT)
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_HI), None)
        self.assertFalse(relay.state()["heat_fan"])

    def test_no_activation_when_pack_already_hot(self):
        relay, rs = self._rs()
        rs.update(_snapshot_preventive(pack_c=_HOT_PACK, soc_pct=_SOC_HI), None)
        self.assertFalse(relay.state()["heat_fan"])

    def test_no_activation_when_soc_low(self):
        relay, rs = self._rs()
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_LO), None)
        self.assertFalse(relay.state()["heat_fan"])

    def test_cuts_out_when_pack_reaches_cutoff(self):
        relay, rs = self._rs()
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_HI), None)
        rs.update(_snapshot_preventive(pack_c=_HOT_PACK, soc_pct=_SOC_HI), None)
        self.assertFalse(relay.state()["heat_fan"])

    def test_cuts_out_when_soc_drops(self):
        relay, rs = self._rs()
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_HI), None)
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_LO), None)
        self.assertFalse(relay.state()["heat_fan"])

    def test_cuts_out_when_ambient_warms(self):
        ambient = [_COLD_AMBIENT]
        relay = StubRelay()
        rs = RelaySupervisor(relay, ambient_temp_fn=lambda _: ambient[0])
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_HI), None)
        self.assertTrue(relay.state()["heat_fan"])
        ambient[0] = _WARM_AMBIENT
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_HI), None)
        self.assertFalse(relay.state()["heat_fan"])

    def test_no_activation_without_battery_data(self):
        relay, rs = self._rs()
        rs.update(make_snapshot(battery=None), None)
        self.assertFalse(relay.state()["heat_fan"])

    def test_no_activation_without_ambient_reading(self):
        relay, rs = self._rs(ambient_c=None)
        rs.update(_snapshot_preventive(pack_c=_COOL_PACK, soc_pct=_SOC_HI), None)
        self.assertFalse(relay.state()["heat_fan"])

    def test_reactive_keeps_relay_on_when_preventive_turns_off(self):
        # Reactive mode is ON (cold + high VOC). Preventive is also on.
        # SOC drops — preventive turns off, but reactive keeps the relay energised.
        relay = StubRelay()
        rs = RelaySupervisor(relay, ambient_temp_fn=_ambient_fn(_COLD_AMBIENT))
        snap_both = make_snapshot(
            battery=make_battery_snapshot(
                min_cell_temperature_c=_HEAT_ON_TEMP_C - 2.0,
                soc_percent=_SOC_HI,
            ),
            classic=make_classic_telemetry(last_voc_v=_VOC_HI),
        )
        rs.update(snap_both, None)
        self.assertTrue(relay.state()["heat_fan"])

        snap_soc_drop = make_snapshot(
            battery=make_battery_snapshot(
                min_cell_temperature_c=_HEAT_ON_TEMP_C - 2.0,
                soc_percent=_SOC_LO,
            ),
            classic=make_classic_telemetry(last_voc_v=_VOC_HI),
        )
        rs.update(snap_soc_drop, None)
        # Preventive off (SOC dropped) but reactive still on → relay stays on
        self.assertTrue(relay.state()["heat_fan"])


if __name__ == "__main__":
    unittest.main()
