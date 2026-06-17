from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.charge_allocator import (  # noqa: E402
    ChargeAllocatorConfig,
    ChargeCurrentAllocator,
    ChargerAllocationInput,
    charge_allocation_event,
)


class ChargeAllocatorTest(unittest.TestCase):
    def test_budget_includes_household_load_allowance(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=35.0,
            load_current_a=12.0,
            chargers=[
                _charger("classic", actual=20.0, limit=80.0, max_=80.0),
                _charger("epever", actual=20.0, limit=100.0, max_=100.0),
            ],
        )

        self.assertEqual(decision.budget_a, 47.0)
        self.assertEqual(decision.reason, "normal_load_allowance")
        self.assertAlmostEqual(decision.targets["classic"].target_current_a or 0.0, 23.5)
        self.assertAlmostEqual(decision.targets["epever"].target_current_a or 0.0, 23.5)

    def test_apportions_by_recent_actual_output(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=35.0,
            load_current_a=12.0,
            chargers=[
                _charger("classic", actual=30.0, limit=80.0, max_=80.0),
                _charger("epever", actual=10.0, limit=100.0, max_=100.0),
            ],
        )

        self.assertAlmostEqual(decision.targets["classic"].target_current_a or 0.0, 35.2)
        self.assertAlmostEqual(decision.targets["epever"].target_current_a or 0.0, 11.8)

    def test_redistributes_budget_when_one_charger_hits_cap(self) -> None:
        decision = ChargeCurrentAllocator(
            ChargeAllocatorConfig(reserve_a=0.0)
        ).decide(
            bms_ccl_a=50.0,
            charge_enabled=True,
            battery_current_a=40.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=10.0, limit=20.0, max_=20.0),
                _charger("epever", actual=10.0, limit=100.0, max_=100.0),
            ],
        )

        self.assertEqual(decision.targets["classic"].target_current_a, 20.0)
        self.assertEqual(decision.targets["epever"].target_current_a, 30.0)

    def test_charge_ceiling_below_ccl_caps_the_budget(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=10.0,
            load_current_a=5.0,
            chargers=[
                _charger("classic", actual=10.0, limit=80.0, max_=80.0),
                _charger("epever", actual=10.0, limit=100.0, max_=100.0),
            ],
            charge_ceiling_a=20.0,
            charge_ceiling_reason="top-knee taper",
        )

        # effective CCL = min(40, 20) = 20 -> budget 20 + 5 - 5 reserve.
        self.assertEqual(decision.budget_a, 20.0)
        self.assertEqual(decision.reason, "top-knee taper")
        self.assertEqual(decision.charge_ceiling_a, 20.0)

    def test_charge_ceiling_zero_stops_all_chargers(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=0.0,
            load_current_a=5.0,
            chargers=[_charger("classic", actual=0.0, limit=80.0, max_=80.0)],
            charge_ceiling_a=0.0,
            charge_ceiling_reason="full-charge latch",
        )

        self.assertEqual(decision.budget_a, 0.0)
        self.assertEqual(decision.reason, "full-charge latch")
        self.assertEqual(decision.targets["classic"].target_current_a, 0.0)
        self.assertTrue(decision.targets["classic"].disable)

    def test_charge_ceiling_above_ccl_is_ignored(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=10.0,
            load_current_a=12.0,
            chargers=[
                _charger("classic", actual=20.0, limit=80.0, max_=80.0),
                _charger("epever", actual=20.0, limit=100.0, max_=100.0),
            ],
            charge_ceiling_a=60.0,
        )

        self.assertEqual(decision.reason, "normal_load_allowance")
        self.assertEqual(decision.budget_a, 47.0)  # CCL governs, ceiling moot
        self.assertEqual(decision.charge_ceiling_a, 60.0)

    def test_feedback_clamps_budget_when_net_battery_charge_exceeds_ccl(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=46.0,
            load_current_a=12.0,
            chargers=[
                _charger("classic", actual=20.0, limit=80.0, max_=80.0),
                _charger("epever", actual=20.0, limit=100.0, max_=100.0),
            ],
        )

        self.assertEqual(decision.reason, "feedback_clamp")
        self.assertEqual(decision.budget_a, 41.0)

    def test_stops_chargers_when_bms_disables_charge(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=False,
            battery_current_a=0.0,
            load_current_a=10.0,
            chargers=[
                _charger("classic", actual=0.0, limit=80.0, max_=80.0),
                _charger("epever", actual=0.0, limit=100.0, max_=100.0),
            ],
        )

        self.assertEqual(decision.budget_a, 0.0)
        self.assertEqual(decision.targets["classic"].target_current_a, 0.0)
        self.assertTrue(decision.targets["classic"].should_write)
        self.assertEqual(decision.targets["epever"].target_current_a, 0.0)

    def test_missing_bms_ccl_produces_no_write_targets(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=None,
            charge_enabled=True,
            battery_current_a=0.0,
            load_current_a=10.0,
            chargers=[_charger("classic", actual=0.0, limit=80.0, max_=80.0)],
        )

        self.assertIsNone(decision.budget_a)
        self.assertIsNone(decision.targets["classic"].target_current_a)
        self.assertFalse(decision.targets["classic"].should_write)

    def test_weights_by_pv_power_not_throttled_output(self) -> None:
        # classic is the sunnier array (more PV power) but is currently throttled
        # to a lower output; weighting by output would starve it, weighting by PV
        # power gives it the larger share.
        decision = ChargeCurrentAllocator(ChargeAllocatorConfig(reserve_a=0.0)).decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=30.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=10.0, limit=80.0, max_=80.0, pv_power_w=1500.0),
                _charger("epever", actual=30.0, limit=100.0, max_=100.0, pv_power_w=500.0),
            ],
        )

        self.assertEqual(decision.weight_basis, "pv_power")
        # 40 A split 1500:500 -> 30 / 10, despite epever's higher present output.
        self.assertAlmostEqual(decision.targets["classic"].target_current_a or 0.0, 30.0)
        self.assertAlmostEqual(decision.targets["epever"].target_current_a or 0.0, 10.0)

    def test_falls_back_to_output_basis_when_any_pv_power_missing(self) -> None:
        decision = ChargeCurrentAllocator(ChargeAllocatorConfig(reserve_a=0.0)).decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=30.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=30.0, limit=80.0, max_=80.0, pv_power_w=1500.0),
                _charger("epever", actual=10.0, limit=100.0, max_=100.0),  # no pv_power
            ],
        )

        # Mixed availability -> consistent output basis for both, not watts/amps.
        self.assertEqual(decision.weight_basis, "actual_current")
        self.assertAlmostEqual(decision.targets["classic"].target_current_a or 0.0, 30.0)
        self.assertAlmostEqual(decision.targets["epever"].target_current_a or 0.0, 10.0)

    def test_sub_floor_target_disables_instead_of_unachievable_limit(self) -> None:
        # epever's PV share rounds below its 1 A register floor: command off, not
        # a limit it cannot hold. classic keeps the budget.
        decision = ChargeCurrentAllocator(ChargeAllocatorConfig(reserve_a=0.0)).decide(
            bms_ccl_a=20.0,
            charge_enabled=True,
            battery_current_a=15.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=20.0, limit=40.0, max_=80.0, pv_power_w=2000.0),
                _charger("epever", actual=0.2, limit=10.0, max_=100.0, pv_power_w=5.0, min_current_a=1.0),
            ],
        )

        epever = decision.targets["epever"]
        self.assertTrue(epever.disable)
        self.assertEqual(epever.target_current_a, 0.0)
        self.assertTrue(epever.should_write)  # was at 10 A, must be taken off
        self.assertGreater(decision.targets["classic"].target_current_a or 0.0, 19.0)

    def test_offline_charger_gets_no_target(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=20.0,
            load_current_a=0.0,
            chargers=[
                _charger("classic", actual=20.0, limit=80.0, max_=80.0),
                _charger("epever", actual=0.0, limit=100.0, max_=100.0, online=False),
            ],
        )

        self.assertIsNone(decision.targets["epever"].target_current_a)
        self.assertEqual(decision.targets["classic"].target_current_a, 35.0)


class ChargeAllocationEventTest(unittest.TestCase):
    def test_event_carries_decision_context(self) -> None:
        decision = ChargeCurrentAllocator().decide(
            bms_ccl_a=40.0,
            charge_enabled=True,
            battery_current_a=35.0,
            load_current_a=12.0,
            chargers=[
                _charger("classic", actual=20.0, limit=80.0, max_=80.0),
                _charger("epever", actual=20.0, limit=100.0, max_=100.0),
            ],
        )

        event = charge_allocation_event(decision, dry_run=True)

        self.assertEqual(event.source, "charge_allocator")
        self.assertEqual(event.event, "allocation_decision")
        detail = event.detail or {}
        self.assertEqual(detail["mode"], "dry-run")
        self.assertEqual(detail["bms_ccl_a"], 40.0)
        self.assertEqual(detail["budget_a"], 47.0)
        self.assertEqual(detail["reason"], "normal_load_allowance")
        self.assertEqual(detail["weight_basis"], "actual_current")
        self.assertEqual(set(detail["targets"]), {"classic", "epever"})
        self.assertAlmostEqual(detail["targets"]["classic"]["target_a"], 23.5)
        self.assertIn("disable", detail["targets"]["classic"])
        self.assertTrue(event.event_id())  # hashes without error


def _charger(
    name: str,
    *,
    actual: float,
    limit: float,
    max_: float,
    online: bool = True,
    pv_power_w: float | None = None,
    min_current_a: float = 0.0,
    enabled: bool = True,
    active: bool = True,
) -> ChargerAllocationInput:
    return ChargerAllocationInput(
        name=name,
        actual_current_a=actual,
        current_limit_a=limit,
        max_current_a=max_,
        pv_power_w=pv_power_w,
        min_current_a=min_current_a,
        online=online,
        enabled=enabled,
        active=active,
    )


if __name__ == "__main__":
    unittest.main()
