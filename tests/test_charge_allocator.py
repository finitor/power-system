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


def _charger(
    name: str,
    *,
    actual: float,
    limit: float,
    max_: float,
    online: bool = True,
) -> ChargerAllocationInput:
    return ChargerAllocationInput(
        name=name,
        actual_current_a=actual,
        current_limit_a=limit,
        max_current_a=max_,
        online=online,
    )


if __name__ == "__main__":
    unittest.main()
