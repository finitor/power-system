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
    ChargeAllocationLogger,
    _allocation_inputs,
    _can_charge,
    _pv_power_w,
)
from offgrid_power.charge_allocator import ChargeCurrentAllocator  # noqa: E402
from offgrid_power.canbus import CanFrame, decode_pylon_snapshot  # noqa: E402
from snapshot_helpers import (  # noqa: E402
    make_battery_snapshot,
    make_classic_telemetry,
    make_epever_telemetry,
    make_snapshot,
)


def _battery_with_limits(*, ccl_a: float, charge_enable: bool, current_a: float = 10.0):
    """Battery snapshot carrying a CCL (0x351), pack current (0x356), and the
    charge-enable request flag (0x35C) -- exercises the request_flags path."""
    ccl_raw = int(round(ccl_a * 10)) & 0xFFFF
    cur_raw = int(round(current_a * 10)) & 0xFFFF
    return decode_pylon_snapshot(
        [
            CanFrame(0x351, bytes([0x48, 0x02, ccl_raw & 0xFF, ccl_raw >> 8, 0, 0, 0, 0])),
            # 52.0 V: below the top-knee, so the charge ceiling doesn't bind and
            # this test stays focused on the CCL / charge-enable path.
            CanFrame(0x356, bytes([0x08, 0x02, cur_raw & 0xFF, cur_raw >> 8, 0, 0, 0, 0])),
            CanFrame(0x35C, bytes([0x80 if charge_enable else 0x00, 0, 0, 0, 0, 0, 0, 0])),
            # Populate status too, so reusing the wrong attribute (status vs
            # request_flags) for charge-enable would throw and fail the test.
            CanFrame(0x359, bytes(8)),
        ]
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

        ChargeAllocationLogger(ChargeCurrentAllocator()).record(snapshot, recorder)

        self.assertEqual(len(recorder.events), 1)
        event = recorder.events[0]
        self.assertEqual(event.source, "charge_allocator")
        self.assertEqual((event.detail or {})["reason"], "missing BMS CCL")

    def test_throttles_unchanged_decisions_but_keeps_a_heartbeat(self) -> None:
        snapshot = make_snapshot(
            battery=make_battery_snapshot(soc_percent=90),
            classic=make_classic_telemetry(pv_voltage_v=120.0, battery_voltage_v=54.0),
        )
        recorder = _FakeRecorder()
        # Large heartbeat so only the first (changed) decision logs.
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), heartbeat_s=10_000.0)

        for _ in range(5):
            logger.record(snapshot, recorder)

        self.assertEqual(len(recorder.events), 1)  # identical decisions suppressed

        # Zero heartbeat -> every poll logs (heartbeat always due).
        recorder2 = _FakeRecorder()
        beat = ChargeAllocationLogger(ChargeCurrentAllocator(), heartbeat_s=0.0)
        for _ in range(3):
            beat.record(snapshot, recorder2)
        self.assertEqual(len(recorder2.events), 3)

    def test_logs_real_allocation_with_ccl_and_request_flags(self) -> None:
        # Full happy path: CCL present, charge enabled via request_flags (the
        # field the live PylonStatus bug got wrong), both arrays producing.
        snapshot = make_snapshot(
            battery=_battery_with_limits(ccl_a=40.0, charge_enable=True, current_a=10.0),
            classic=make_classic_telemetry(
                pv_voltage_v=120.0, pv_current_a=6.0, battery_voltage_v=54.0, battery_current_a=20.0
            ),
            epever=make_epever_telemetry(
                pv_voltage_v=160.0, pv_current_a=3.0, battery_voltage_v=54.0, battery_current_a=12.0
            ),
        )
        recorder = _FakeRecorder()

        ChargeAllocationLogger(ChargeCurrentAllocator()).record(snapshot, recorder)

        self.assertEqual(len(recorder.events), 1)
        detail = recorder.events[0].detail or {}
        self.assertEqual(detail["reason"], "normal_load_allowance")
        self.assertEqual(detail["bms_ccl_a"], 40.0)
        self.assertEqual(set(detail["targets"]), {"classic", "epever"})

    def test_no_logger_is_a_noop(self) -> None:
        recorder = _FakeRecorder()
        logger = ChargeAllocationLogger(ChargeCurrentAllocator())
        logger.record(make_snapshot(), recorder)  # no chargers -> nothing logged
        self.assertEqual(recorder.events, [])


if __name__ == "__main__":
    unittest.main()
