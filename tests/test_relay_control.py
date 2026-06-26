from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offgrid_power.canbus import CanFrame, PylonMeasurements, PylonCanSnapshot, decode_pylon_snapshot
from offgrid_power.charge_allocator import ChargeAllocationDecision, ChargerAllocationTarget
from offgrid_power.relay_control import RelaySupervisor
from snapshot_helpers import make_classic_telemetry, make_snapshot


class StubRelay:
    def __init__(self):
        self._state = {"heat_fan": False, "charge_disable": False}

    def set(self, name, on):
        self._state[name] = on

    def state(self):
        return dict(self._state)

    is_stub = True


def _snapshot(temp_c=10.0, voc_v=120.0):
    battery = decode_pylon_snapshot([
        CanFrame(0x356, _encode_measurements(temp_c)),
    ])
    classic = make_classic_telemetry(last_voc_v=voc_v)
    return make_snapshot(battery=battery, classic=classic)


def _encode_measurements(temp_c: float) -> bytes:
    # 0x356: voltage (2B), current (2B), temperature (2B) all s16 * 0.1
    temp_raw = round(temp_c * 10)
    return b"\x00\x00\x00\x00" + temp_raw.to_bytes(2, "big", signed=True) + b"\x00\x00"


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


class TestHeatFanHysteresis(unittest.TestCase):
    def setUp(self):
        self.relay = StubRelay()
        self.rs = RelaySupervisor(self.relay)

    def test_activates_when_cold_and_sunny(self):
        self.rs.update(_snapshot(temp_c=-1.0, voc_v=135.0), None)
        self.assertTrue(self.relay.state()["heat_fan"])

    def test_no_activation_warm(self):
        self.rs.update(_snapshot(temp_c=2.0, voc_v=135.0), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_no_activation_low_voc(self):
        self.rs.update(_snapshot(temp_c=-1.0, voc_v=133.0), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_stays_on_within_hysteresis(self):
        self.rs.update(_snapshot(temp_c=-1.0, voc_v=135.0), None)
        # VOC drops to between 130 and 134 — should stay on
        self.rs.update(_snapshot(temp_c=-1.0, voc_v=132.0), None)
        self.assertTrue(self.relay.state()["heat_fan"])

    def test_cuts_out_below_voc_floor(self):
        self.rs.update(_snapshot(temp_c=-1.0, voc_v=135.0), None)
        self.rs.update(_snapshot(temp_c=-1.0, voc_v=129.0), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_cuts_out_when_warm(self):
        self.rs.update(_snapshot(temp_c=-1.0, voc_v=135.0), None)
        self.rs.update(_snapshot(temp_c=6.0, voc_v=135.0), None)
        self.assertFalse(self.relay.state()["heat_fan"])

    def test_does_not_reactivate_within_hysteresis(self):
        # Turn on, then warm up to 6°C (cuts out), then cool back to 3°C — stays off
        self.rs.update(_snapshot(temp_c=-1.0, voc_v=135.0), None)
        self.rs.update(_snapshot(temp_c=6.0, voc_v=135.0), None)
        self.rs.update(_snapshot(temp_c=3.0, voc_v=135.0), None)
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


if __name__ == "__main__":
    unittest.main()
