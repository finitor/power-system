from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offgrid_power.charge_allocator import AllocationOverride, ChargeAllocationDecision, ChargerAllocationTarget
from offgrid_power.relay_control import RelaySupervisor, _HEAT_ON_TEMP_C, _HEAT_OFF_TEMP_C, _HEAT_ON_VOC_V, _HEAT_OFF_VOC_V
from snapshot_helpers import make_battery_snapshot, make_classic_telemetry, make_snapshot


class StubRelay:
    def __init__(self):
        self._state = {"heat_fan": False, "charge_disable": False}

    def set(self, name, on):
        self._state[name] = on

    def state(self):
        return dict(self._state)

    is_stub = True


def _snapshot(temp_c=10.0, voc_v=120.0):
    battery = make_battery_snapshot(min_cell_temperature_c=temp_c)
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


if __name__ == "__main__":
    unittest.main()
