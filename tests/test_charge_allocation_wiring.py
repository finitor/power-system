"""Wiring tests: snapshot -> allocator inputs, eligibility, and dry-run logging."""

from __future__ import annotations

from datetime import timedelta
import os
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
    _config_from_env,
    _pv_power_w,
    _with_derived_epever_today,
)
from offgrid_power.charge_ceiling import ChargeCeilingConfig  # noqa: E402
from offgrid_power.charge_allocator import (  # noqa: E402
    ChargeAllocatorConfig,
    ChargeCurrentAllocator,
    ChargerAllocationInput,
)


def _charger(name, *, actual, limit, max_, min_current_a=0.0):
    return ChargerAllocationInput(
        name=name,
        actual_current_a=actual,
        current_limit_a=limit,
        max_current_a=max_,
        min_current_a=min_current_a,
    )
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
    temp_raw = int(round(20.0 * 10)) & 0xFFFF
    return decode_pylon_snapshot(
        [
            CanFrame(0x351, bytes([0x48, 0x02, ccl_raw & 0xFF, ccl_raw >> 8, 0, 0, 0, 0])),
            # 52.0 V: ordinary pack voltage; this test stays focused on the CCL
            # / charge-enable path.
            CanFrame(
                0x356,
                bytes(
                    [
                        0x08,
                        0x02,
                        cur_raw & 0xFF,
                        cur_raw >> 8,
                        temp_raw & 0xFF,
                        temp_raw >> 8,
                        0,
                        0,
                    ]
                ),
            ),
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


class _FakeSupervisor:
    def __init__(self) -> None:
        self.classic_writes: list = []
        self.epever_currents: list = []
        self.coil: list = []

    def write_classic_charge_settings(self, **kwargs):
        self.classic_writes.append(kwargs)

    def write_epever_max_charging_current(self, current_a):
        self.epever_currents.append(current_a)

    def set_epever_charging(self, enabled):
        self.coil.append(enabled)


class _FailingClassicSupervisor(_FakeSupervisor):
    def write_classic_charge_settings(self, **kwargs):
        raise RuntimeError("classic write failed")


class LiveApplyTest(unittest.TestCase):
    def test_writes_limits_and_turns_the_coil_on_once(self) -> None:
        sup = _FakeSupervisor()
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), supervisor=sup, live=True)
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=10.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=10.0, limit=10.0, max_=80.0),
                _charger("epever", actual=10.0, limit=10.0, max_=100.0),
            ],
        )

        logger._apply(decision, {"classic": 10.0, "epever": 10.0})
        logger._apply(decision, {"classic": 10.0, "epever": 10.0})

        # Classic limit written volatile; EPEver current written; coil toggled
        # on exactly once despite two cycles.
        self.assertTrue(sup.classic_writes)
        self.assertFalse(sup.classic_writes[0]["persist"])
        self.assertTrue(sup.epever_currents)
        self.assertEqual(sup.coil, [True])

    def test_disable_uses_coil_off_for_epever_and_zero_limit_for_classic(self) -> None:
        sup = _FakeSupervisor()
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), supervisor=sup, live=True)
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=False,  # BMS disabled -> stop everything
            battery_current_a=0.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=0.0, limit=80.0, max_=80.0),
                _charger("epever", actual=0.0, limit=80.0, max_=100.0, min_current_a=1.0),
            ],
            charge_ceiling_a=0.0,
            charge_ceiling_reason="BMS charge disabled",
        )

        logger._apply(decision, {"classic": 80.0, "epever": 80.0})

        self.assertEqual(sup.coil, [False])  # EPEver off via coil
        self.assertIn({"battery_current_limit_a": 0.0, "persist": False}, sup.classic_writes)
        self.assertEqual(sup.epever_currents, [])  # no current write while disabled

    def test_reenables_epever_coil_even_when_limit_is_already_correct(self) -> None:
        sup = _FakeSupervisor()
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), supervisor=sup, live=True)
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=200.0,
            charge_enabled=True,
            battery_current_a=0.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=0.0, limit=100.0, max_=100.0),
                _charger("epever", actual=0.0, limit=100.0, max_=100.0, min_current_a=1.0),
            ],
        )

        self.assertFalse(decision.targets["epever"].should_write)

        logger._apply(decision, {"classic": 100.0, "epever": 100.0})

        self.assertEqual(sup.coil, [True])
        self.assertEqual(sup.epever_currents, [])

    def test_records_successful_live_control_events(self) -> None:
        sup = _FakeSupervisor()
        recorder = _FakeRecorder()
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), supervisor=sup, live=True)
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=10.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=10.0, limit=10.0, max_=80.0),
                _charger("epever", actual=10.0, limit=10.0, max_=100.0),
            ],
        )

        logger._apply(
            decision,
            {"classic": 10.0, "epever": 10.0},
            recorder=recorder,
        )

        events = {(event.event, (event.detail or {}).get("controller")): event for event in recorder.events}
        self.assertIn(("charge_enable_write", "epever"), events)
        self.assertIn(("limit_write", "classic"), events)
        self.assertIn(("limit_write", "epever"), events)
        coil_detail = events[("charge_enable_write", "epever")].detail or {}
        self.assertEqual(coil_detail["action"], "charge_enable")
        self.assertTrue(coil_detail["enabled"])
        self.assertTrue(coil_detail["success"])
        limit_detail = events[("limit_write", "classic")].detail or {}
        self.assertEqual(limit_detail["action"], "current_limit")
        self.assertEqual(limit_detail["previous_a"], 10.0)
        self.assertTrue(limit_detail["success"])

    def test_records_failed_live_control_event_without_raising(self) -> None:
        sup = _FailingClassicSupervisor()
        recorder = _FakeRecorder()
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), supervisor=sup, live=True)
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=False,
            battery_current_a=0.0,
            load_current_a=0.0,
            chargers=[_charger("classic", actual=0.0, limit=80.0, max_=80.0)],
            charge_ceiling_a=0.0,
            charge_ceiling_reason="BMS charge disabled",
        )

        logger._apply(decision, {"classic": 80.0}, recorder=recorder)

        self.assertEqual(len(recorder.events), 1)
        event = recorder.events[0]
        self.assertEqual(event.event, "limit_write")
        detail = event.detail or {}
        self.assertEqual(detail["controller"], "classic")
        self.assertEqual(detail["target_a"], 0.0)
        self.assertFalse(detail["success"])
        self.assertIn("classic write failed", detail["error"])


class ConfigFromEnvTest(unittest.TestCase):
    def test_overrides_named_fields_and_keeps_defaults(self) -> None:
        import os

        os.environ["CHARGE_CEILING_BMS_CCL_SCALING_FACTOR"] = "0.6"
        os.environ["CHARGE_CEILING_HIGH_CELL_STOP_V"] = "3.54"
        os.environ.pop("CHARGE_CEILING_BMS_KNEE_CCL_BASELINE_A", None)
        try:
            config = _config_from_env(ChargeCeilingConfig, "CHARGE_CEILING_")
        finally:
            del os.environ["CHARGE_CEILING_BMS_CCL_SCALING_FACTOR"]
            del os.environ["CHARGE_CEILING_HIGH_CELL_STOP_V"]

        self.assertEqual(config.bms_ccl_scaling_factor, 0.6)  # overridden
        self.assertEqual(config.high_cell_stop_v, 3.54)  # overridden
        self.assertEqual(config.bms_knee_ccl_baseline_a, 200.0)  # default preserved

    def test_non_numeric_value_is_ignored(self) -> None:
        import os

        os.environ["CHARGE_CEILING_BMS_CCL_SCALING_FACTOR"] = "oops"
        try:
            config = _config_from_env(ChargeCeilingConfig, "CHARGE_CEILING_")
        finally:
            del os.environ["CHARGE_CEILING_BMS_CCL_SCALING_FACTOR"]
        self.assertEqual(config.bms_ccl_scaling_factor, 0.5)  # falls back to default


class TargetStabilizationTest(unittest.TestCase):
    def test_holds_target_inside_deadband(self) -> None:
        logger = ChargeAllocationLogger(
            ChargeCurrentAllocator(ChargeAllocatorConfig(reserve_a=0.0)),
            target_deadband_a=5.0,
            target_quantum_a=5.0,
        )
        charger = _charger("classic", actual=10.0, limit=17.0, max_=100.0)
        decision = logger.allocator.decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=10.0,
            load_current_a=0.0,
            chargers=[charger],
            charge_ceiling_a=20.0,
            charge_ceiling_reason="BMS CCL fraction",
        )

        stabilized = logger._stabilized_decision(decision, [charger])

        target = stabilized.targets["classic"]
        self.assertEqual(target.target_current_a, 17.0)
        self.assertFalse(target.should_write)

    def test_equal_split_rebalances_stale_controller_spread(self) -> None:
        logger = ChargeAllocationLogger(
            ChargeCurrentAllocator(ChargeAllocatorConfig(reserve_a=0.0)),
            target_deadband_a=5.0,
            target_quantum_a=5.0,
        )
        chargers = [
            _charger("classic", actual=0.0, limit=10.0, max_=80.0),
            _charger("epever", actual=0.0, limit=15.0, max_=100.0),
        ]
        decision = logger.allocator.decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=0.0,
            load_current_a=4.0,
            chargers=chargers,
            charge_ceiling_a=20.0,
            charge_ceiling_reason="BMS CCL fraction",
        )

        stabilized = logger._stabilized_decision(decision, chargers)

        self.assertEqual(stabilized.weight_basis, "equal")
        self.assertEqual(stabilized.targets["classic"].target_current_a, 12.0)
        self.assertTrue(stabilized.targets["classic"].should_write)
        self.assertEqual(stabilized.targets["epever"].target_current_a, 12.0)
        self.assertTrue(stabilized.targets["epever"].should_write)

    def test_quantizes_large_target_changes(self) -> None:
        logger = ChargeAllocationLogger(
            ChargeCurrentAllocator(ChargeAllocatorConfig(reserve_a=0.0)),
            target_deadband_a=5.0,
            target_quantum_a=5.0,
        )
        charger = _charger("classic", actual=10.0, limit=10.0, max_=100.0)
        decision = logger.allocator.decide(
            bms_ccl_a=44.0,
            charge_enabled=True,
            battery_current_a=10.0,
            load_current_a=0.0,
            chargers=[charger],
            charge_ceiling_a=22.0,
            charge_ceiling_reason="BMS CCL fraction",
        )

        stabilized = logger._stabilized_decision(decision, [charger])

        target = stabilized.targets["classic"]
        self.assertEqual(target.target_current_a, 20.0)
        self.assertTrue(target.should_write)

    def test_real_stop_bypasses_target_smoothing(self) -> None:
        logger = ChargeAllocationLogger(
            ChargeCurrentAllocator(),
            target_deadband_a=100.0,
            target_quantum_a=5.0,
        )
        charger = _charger("classic", actual=10.0, limit=80.0, max_=100.0)
        decision = logger.allocator.decide(
            bms_ccl_a=40.0,
            charge_enabled=False,
            battery_current_a=10.0,
            load_current_a=0.0,
            chargers=[charger],
            charge_ceiling_a=0.0,
            charge_ceiling_reason="BMS charge disabled",
        )

        stabilized = logger._stabilized_decision(decision, [charger])

        target = stabilized.targets["classic"]
        self.assertEqual(target.target_current_a, 0.0)
        self.assertTrue(target.disable)
        self.assertTrue(target.should_write)


class ControllerSleepDebounceTest(unittest.TestCase):
    def test_epever_resting_must_persist_before_it_is_marked_inactive(self) -> None:
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), epever_sleep_debounce_s=120.0)
        t0 = make_snapshot().captured_at
        sleeping = ChargerAllocationInput(
            name="epever",
            actual_current_a=0.0,
            current_limit_a=5.0,
            max_current_a=100.0,
            min_current_a=1.0,
            active=False,
        )

        first = logger._debounced_inputs([sleeping], t0)[0]
        held = logger._debounced_inputs([sleeping], t0 + timedelta(seconds=119))[0]
        released = logger._debounced_inputs([sleeping], t0 + timedelta(seconds=120))[0]

        self.assertTrue(first.active)
        self.assertTrue(held.active)
        self.assertFalse(released.active)

    def test_epever_active_stage_resets_sleep_debounce(self) -> None:
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), epever_sleep_debounce_s=120.0)
        t0 = make_snapshot().captured_at
        sleeping = ChargerAllocationInput(
            name="epever",
            actual_current_a=0.0,
            current_limit_a=5.0,
            max_current_a=100.0,
            min_current_a=1.0,
            active=False,
        )
        awake = type(sleeping)(**{**sleeping.__dict__, "active": True})

        logger._debounced_inputs([sleeping], t0)
        self.assertTrue(logger._debounced_inputs([awake], t0 + timedelta(seconds=60))[0].active)
        self.assertTrue(logger._debounced_inputs([sleeping], t0 + timedelta(seconds=61))[0].active)

    def test_classic_resting_must_persist_before_it_is_marked_inactive(self) -> None:
        logger = ChargeAllocationLogger(ChargeCurrentAllocator(), classic_sleep_debounce_s=120.0)
        t0 = make_snapshot().captured_at
        sleeping = ChargerAllocationInput(
            name="classic",
            actual_current_a=0.0,
            current_limit_a=5.0,
            max_current_a=100.0,
            active=False,
        )

        first = logger._debounced_inputs([sleeping], t0)[0]
        released = logger._debounced_inputs([sleeping], t0 + timedelta(seconds=120))[0]

        self.assertTrue(first.active)
        self.assertFalse(released.active)


class _FakeMidnightRecorder:
    def __init__(self, value):
        self.value = value

    def midnight_metric_value(self, source, metric, day):
        return self.value


class DerivedEpeverTodayTest(unittest.TestCase):
    def test_derives_today_from_lifetime_total_minus_midnight(self) -> None:
        snap = make_snapshot(epever=make_epever_telemetry(generated_total_kwh=3.52))
        out = _with_derived_epever_today(snap, _FakeMidnightRecorder(3.51))
        self.assertAlmostEqual(out.epever.generated_today_kwh, 0.01)

    def test_today_unavailable_without_midnight_baseline(self) -> None:
        # The raw register (9.9) is unreliable; without a baseline it must be
        # suppressed and flagged, not shown.
        snap = make_snapshot(
            epever=make_epever_telemetry(generated_total_kwh=3.52, generated_today_kwh=9.9)
        )
        out = _with_derived_epever_today(snap, _FakeMidnightRecorder(None))
        self.assertIsNone(out.epever.generated_today_kwh)
        self.assertEqual(
            out.epever.generated_today_unavailable_reason,
            "unavailable, midnight cumulative energy was not logged",
        )

    def test_today_unavailable_without_lifetime_total(self) -> None:
        snap = make_snapshot(epever=make_epever_telemetry(generated_total_kwh=None, generated_today_kwh=9.9))
        out = _with_derived_epever_today(snap, _FakeMidnightRecorder(3.0))
        self.assertIsNone(out.epever.generated_today_kwh)
        self.assertIsNotNone(out.epever.generated_today_unavailable_reason)


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
                pv_voltage_v=120.0,
                pv_current_a=5.0,
                battery_voltage_v=54.0,
                battery_current_a=18.0,
                charge_stage="BulkMppt",
            ),
            epever=make_epever_telemetry(
                pv_voltage_v=160.0,
                pv_current_a=2.0,
                battery_voltage_v=54.0,
                battery_current_a=8.0,
                charging_status="Boost",
            ),
        )

        inputs = {c.name: c for c in _allocation_inputs(snapshot)}

        self.assertEqual(set(inputs), {"classic", "epever"})
        self.assertEqual(inputs["classic"].min_current_a, 0.0)
        self.assertEqual(inputs["epever"].min_current_a, 1.0)  # 0x9013 floor
        self.assertEqual(inputs["classic"].max_current_a, 80.0)
        self.assertEqual(inputs["epever"].max_current_a, 100.0)
        self.assertEqual(inputs["classic"].pv_power_w, 600.0)
        self.assertTrue(inputs["classic"].active)
        self.assertTrue(inputs["epever"].active)

    def test_allocation_input_maxima_can_be_overridden_by_env(self) -> None:
        os.environ["CHARGE_ALLOC_CLASSIC_MAX_A"] = "70"
        os.environ["CHARGE_ALLOC_EPEVER_MAX_A"] = "90"
        try:
            snapshot = make_snapshot(
                classic=make_classic_telemetry(),
                epever=make_epever_telemetry(rated_charging_current_a=None),
            )
            inputs = {c.name: c for c in _allocation_inputs(snapshot)}
        finally:
            del os.environ["CHARGE_ALLOC_CLASSIC_MAX_A"]
            del os.environ["CHARGE_ALLOC_EPEVER_MAX_A"]

        self.assertEqual(inputs["classic"].max_current_a, 70.0)
        self.assertEqual(inputs["epever"].max_current_a, 90.0)

    def test_classic_resting_state_marks_it_inactive_even_with_high_pv_voltage(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(
                pv_voltage_v=160.0,
                pv_current_a=0.0,
                battery_voltage_v=54.0,
                charge_stage="Resting",
            ),
        )

        inputs = {c.name: c for c in _allocation_inputs(snapshot)}

        self.assertFalse(inputs["classic"].active)

    def test_epever_resting_state_marks_it_inactive_even_with_high_pv_voltage(self) -> None:
        snapshot = make_snapshot(
            epever=make_epever_telemetry(
                pv_voltage_v=160.0,
                pv_current_a=0.0,
                pv_power_w=0.0,
                battery_voltage_v=54.0,
                charging_status="No charging",
            ),
        )

        inputs = {c.name: c for c in _allocation_inputs(snapshot)}

        self.assertFalse(inputs["epever"].active)

    def test_classic_active_stage_stays_eligible_even_when_pv_voltage_is_low(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(
                pv_voltage_v=28.0,
                battery_voltage_v=54.0,
                charge_stage="BulkMppt",
            )
        )
        inputs = {c.name: c for c in _allocation_inputs(snapshot)}
        self.assertTrue(inputs["classic"].active)

    def test_classic_active_stage_becomes_inactive_when_pv_is_zero(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(
                pv_voltage_v=0.0,
                battery_voltage_v=54.0,
                charge_stage="BulkMppt",
            )
        )
        inputs = {c.name: c for c in _allocation_inputs(snapshot)}
        self.assertFalse(inputs["classic"].active)

    def test_epever_active_stage_becomes_inactive_when_pv_is_zero(self) -> None:
        snapshot = make_snapshot(
            epever=make_epever_telemetry(
                pv_voltage_v=0.0,
                battery_voltage_v=54.0,
                charging_status="Boost",
            )
        )
        inputs = {c.name: c for c in _allocation_inputs(snapshot)}
        self.assertFalse(inputs["epever"].active)

    def test_epever_active_stage_becomes_inactive_when_pv_is_none(self) -> None:
        # RS485 read failure (None) must not allow Epever into the eligible pool —
        # a stale active stage + no PV reading should not steal Classic's budget.
        snapshot = make_snapshot(
            epever=make_epever_telemetry(
                pv_voltage_v=None,
                battery_voltage_v=54.0,
                charging_status="Boost",
            )
        )
        inputs = {c.name: c for c in _allocation_inputs(snapshot)}
        self.assertFalse(inputs["epever"].active)


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
                pv_voltage_v=120.0,
                pv_current_a=6.0,
                battery_voltage_v=54.0,
                battery_current_a=20.0,
                charge_stage="BulkMppt",
            ),
            epever=make_epever_telemetry(
                pv_voltage_v=160.0,
                pv_current_a=3.0,
                battery_voltage_v=54.0,
                battery_current_a=12.0,
                charging_status="Boost",
            ),
        )
        recorder = _FakeRecorder()

        ChargeAllocationLogger(ChargeCurrentAllocator()).record(snapshot, recorder)

        self.assertEqual(len(recorder.events), 1)
        detail = recorder.events[0].detail or {}
        self.assertEqual(detail["reason"], "BMS CCL fraction")
        self.assertEqual(detail["bms_ccl_a"], 40.0)
        self.assertEqual(detail["battery_current_a"], 10.0)
        self.assertEqual(set(detail["targets"]), {"classic", "epever"})

    def test_offline_classic_allows_epever_to_receive_whole_budget(self) -> None:
        snapshot = make_snapshot(
            battery=_battery_with_limits(ccl_a=40.0, charge_enable=True, current_a=0.0),
            classic=None,
            epever=make_epever_telemetry(
                battery_current_a=4.0,
                charging_status="Boost",
            ),
        )
        recorder = _FakeRecorder()

        ChargeAllocationLogger(ChargeCurrentAllocator()).record(snapshot, recorder)

        detail = recorder.events[0].detail or {}
        self.assertEqual(detail["reason"], "BMS CCL fraction")
        self.assertEqual(detail["load_allowance_a"], 4.0)
        self.assertEqual(detail["budget_a"], 24.0)
        self.assertEqual(set(detail["targets"]), {"epever"})
        self.assertEqual(detail["targets"]["epever"]["target_a"], 24.0)

    def test_no_logger_is_a_noop(self) -> None:
        recorder = _FakeRecorder()
        logger = ChargeAllocationLogger(ChargeCurrentAllocator())
        logger.record(make_snapshot(), recorder)  # no chargers -> nothing logged
        self.assertEqual(recorder.events, [])


if __name__ == "__main__":
    unittest.main()
