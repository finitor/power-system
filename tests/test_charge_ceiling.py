from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import (  # noqa: E402
    PylonCanSnapshot,
    PylonChargeLimits,
    PylonExtendedMeasurements,
    PylonMeasurements,
    PylonRequestFlags,
    PylonStateOfCharge,
)
from offgrid_power.charge_ceiling import ChargeCeiling  # noqa: E402


def _battery(
    *,
    ccl=200.0,
    charge_enabled=True,
    soc=None,
    voltage=None,
    max_cell=None,
    min_cell=None,
    min_cell_temp=None,
    pack_temp=20.0,
) -> PylonCanSnapshot:
    return PylonCanSnapshot(
        charge_limits=None
        if ccl is None
        else PylonChargeLimits(
            charge_voltage_limit_v=58.4,
            charge_current_limit_a=ccl,
            discharge_current_limit_a=200.0,
            discharge_voltage_limit_v=44.8,
        ),
        request_flags=PylonRequestFlags(
            charge_enable=charge_enabled,
            discharge_enable=True,
            force_charge_1=False,
            force_charge_2=False,
            full_charge_request=False,
        ),
        state_of_charge=None if soc is None else PylonStateOfCharge(soc_percent=soc, soh_percent=100),
        measurements=None if voltage is None else PylonMeasurements(voltage_v=voltage, current_a=0.0, temperature_c=pack_temp),
        extended_measurements=PylonExtendedMeasurements(
            min_cell_voltage_v=min_cell,
            max_cell_voltage_v=max_cell,
            min_cell_temperature_c=min_cell_temp,
        ),
    )


class ChargeCeilingTest(unittest.TestCase):
    def test_baseline_bms_ccl_has_no_ceiling(self) -> None:
        result = ChargeCeiling().evaluate(_battery(ccl=200.0, soc=94, voltage=56.0))
        self.assertIsNone(result.ceiling_a)
        self.assertEqual(result.reason, "unconstrained")

    def test_reduced_bms_ccl_sets_fractional_budget(self) -> None:
        result = ChargeCeiling().evaluate(_battery(ccl=40.0, soc=94, voltage=56.0))
        self.assertEqual(result.reason, "BMS CCL fraction")
        self.assertEqual(result.ceiling_a, 20.0)

    def test_bms_charge_disabled_is_a_hard_stop(self) -> None:
        result = ChargeCeiling().evaluate(_battery(ccl=40.0), charge_enabled=False)
        self.assertEqual(result.ceiling_a, 0.0)
        self.assertEqual(result.reason, "BMS charge disabled")

    def test_bms_ccl_zero_is_a_hard_stop(self) -> None:
        result = ChargeCeiling().evaluate(_battery(ccl=0.0))
        self.assertEqual(result.ceiling_a, 0.0)
        self.assertEqual(result.reason, "BMS CCL is zero")

    def test_high_cell_voltage_is_a_hard_stop(self) -> None:
        result = ChargeCeiling().evaluate(_battery(soc=90, voltage=54.0, max_cell=3.63, min_cell=3.56))
        self.assertEqual(result.ceiling_a, 0.0)
        self.assertIn("max cell", result.reason)

    def test_high_cell_delta_is_a_hard_stop(self) -> None:
        result = ChargeCeiling().evaluate(_battery(soc=90, voltage=54.0, max_cell=3.56, min_cell=3.40))
        self.assertEqual(result.ceiling_a, 0.0)
        self.assertIn("delta", result.reason)

    def test_low_cell_temperature_is_a_hard_stop(self) -> None:
        result = ChargeCeiling().evaluate(_battery(soc=80, voltage=53.0, min_cell_temp=-0.2))
        self.assertEqual(result.ceiling_a, 0.0)
        self.assertIn("battery temp -0.2C", result.reason)

    def test_low_temperature_falls_back_to_pack_temperature(self) -> None:
        result = ChargeCeiling().evaluate(_battery(soc=80, voltage=53.0, pack_temp=-0.1))
        self.assertEqual(result.ceiling_a, 0.0)
        self.assertIn("battery temp -0.1C", result.reason)

    def test_low_temperature_stop_latches_until_recovery_temperature(self) -> None:
        ceiling = ChargeCeiling()
        tripped = ceiling.evaluate(_battery(soc=80, voltage=53.0, min_cell_temp=-0.1))
        self.assertEqual(tripped.ceiling_a, 0.0)
        self.assertTrue(ceiling.low_temp_latched)

        still_latched = ceiling.evaluate(_battery(soc=80, voltage=53.0, min_cell_temp=1.9))
        self.assertEqual(still_latched.ceiling_a, 0.0)
        self.assertIn("battery temp", still_latched.reason)

        cleared = ceiling.evaluate(_battery(ccl=40, soc=80, voltage=53.0, min_cell_temp=2.0))
        self.assertFalse(ceiling.low_temp_latched)
        self.assertEqual(cleared.ceiling_a, 20.0)
        self.assertEqual(cleared.reason, "BMS CCL fraction")

    def test_cell_safety_stop_latches_until_cell_falls_below_soft_limit(self) -> None:
        ceiling = ChargeCeiling()
        tripped = ceiling.evaluate(_battery(soc=90, voltage=54.0, max_cell=3.63, min_cell=3.56))
        self.assertEqual(tripped.ceiling_a, 0.0)
        self.assertTrue(ceiling.cell_latched)

        still_latched = ceiling.evaluate(_battery(ccl=40, soc=90, voltage=54.0, max_cell=3.57, min_cell=3.54))
        self.assertEqual(still_latched.ceiling_a, 0.0)
        self.assertIn("max cell", still_latched.reason)

        cleared = ceiling.evaluate(_battery(ccl=40, soc=90, voltage=54.0, max_cell=3.54, min_cell=3.51))
        self.assertFalse(ceiling.cell_latched)
        self.assertEqual(cleared.ceiling_a, 20.0)
        self.assertEqual(cleared.reason, "BMS CCL fraction")

    def test_full_charge_latch_holds_zero_until_pack_rests_low(self) -> None:
        ceiling = ChargeCeiling()
        # Hits 100% -> latched at zero.
        self.assertEqual(ceiling.evaluate(_battery(soc=100, voltage=55.0)).ceiling_a, 0.0)
        self.assertTrue(ceiling.full_latched)
        # Drops to 96% but still high voltage -> still latched.
        latched = ceiling.evaluate(_battery(soc=96, voltage=54.5))
        self.assertEqual(latched.ceiling_a, 0.0)
        self.assertEqual(latched.reason, "full-charge latch")
        # Rests below reset SOC and reset voltage -> latch clears, budget resumes.
        cleared = ceiling.evaluate(_battery(soc=96, voltage=53.9))
        self.assertFalse(ceiling.full_latched)
        self.assertEqual(cleared.reason, "unconstrained")


if __name__ == "__main__":
    unittest.main()
