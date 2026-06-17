"""Wiring tests: snapshot -> allocator inputs, eligibility, and dry-run logging."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offgrid_power.cli.supervisor_display import (  # noqa: E402
    _allocation_inputs,
    _can_charge,
    _pv_power_w,
    record_charge_allocation,
)
from offgrid_power.charge_allocator import ChargeCurrentAllocator  # noqa: E402
from snapshot_helpers import (  # noqa: E402
    make_battery_snapshot,
    make_classic_telemetry,
    make_epever_telemetry,
    make_snapshot,
)


class _FakeRecorder:
    def __init__(self) -> None:
        self.events: list = []

    def record_event(self, event) -> None:
        self.events.append(event)


class EligibilityTest(unittest.TestCase):
    def test_can_charge_requires_pv_above_bus(self) -> None:
        self.assertTrue(_can_charge(120.0, 54.0))
        self.assertFalse(_can_charge(54.5, 54.0))  # within margin
        self.assertFalse(_can_charge(0.0, 54.0))  # night
        self.assertFalse(_can_charge(None, 54.0))

    def test_pv_power_is_voltage_times_current(self) -> None:
        self.assertEqual(_pv_power_w(120.0, 5.0), 600.0)
        self.assertIsNone(_pv_power_w(None, 5.0))


class AllocationInputsTest(unittest.TestCase):
    def test_maps_both_controllers_with_per_device_floors_and_eligibility(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(
                pv_voltage_v=120.0, pv_current_a=5.0, battery_voltage_v=54.0, battery_current_a=18.0
            ),
            epever=make_epever_telemetry(
                pv_voltage_v=160.0, pv_current_a=2.0, battery_voltage_v=54.0, battery_current_a=8.0
            ),
        )

        inputs = {c.name: c for c in _allocation_inputs(snapshot)}

        self.assertEqual(set(inputs), {"classic", "epever"})
        self.assertEqual(inputs["classic"].min_current_a, 0.0)
        self.assertEqual(inputs["epever"].min_current_a, 1.0)  # 0x9013 floor
        self.assertEqual(inputs["classic"].pv_power_w, 600.0)
        self.assertTrue(inputs["classic"].active)
        self.assertTrue(inputs["epever"].active)

    def test_controller_with_pv_below_bus_is_ineligible(self) -> None:
        # Default classic telemetry pv (28 V) sits below the bus -> not able to
        # charge, so it must be marked ineligible rather than fed budget.
        snapshot = make_snapshot(classic=make_classic_telemetry(battery_voltage_v=54.0))
        inputs = {c.name: c for c in _allocation_inputs(snapshot)}
        self.assertFalse(inputs["classic"].active)


class DryRunRecordingTest(unittest.TestCase):
    def test_logs_decision_event_and_never_raises_without_ccl(self) -> None:
        # make_battery_snapshot has no charge-limit frame, so CCL is missing:
        # the allocator returns a no-write decision and we still log it.
        snapshot = make_snapshot(
            battery=make_battery_snapshot(soc_percent=90),
            classic=make_classic_telemetry(pv_voltage_v=120.0, battery_voltage_v=54.0),
        )
        recorder = _FakeRecorder()

        record_charge_allocation(ChargeCurrentAllocator(), snapshot=snapshot, recorder=recorder)

        self.assertEqual(len(recorder.events), 1)
        event = recorder.events[0]
        self.assertEqual(event.source, "charge_allocator")
        self.assertEqual((event.detail or {})["reason"], "missing BMS CCL")

    def test_no_allocator_is_a_noop(self) -> None:
        recorder = _FakeRecorder()
        record_charge_allocation(None, snapshot=make_snapshot(), recorder=recorder)
        self.assertEqual(recorder.events, [])


if __name__ == "__main__":
    unittest.main()
