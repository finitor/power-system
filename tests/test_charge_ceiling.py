from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import (  # noqa: E402
    PylonCanSnapshot,
    PylonExtendedMeasurements,
    PylonMeasurements,
    PylonStateOfCharge,
)
from offgrid_power.charge_ceiling import ChargeCeiling  # noqa: E402


def _battery(*, soc=None, voltage=None, max_cell=None, min_cell=None) -> PylonCanSnapshot:
    return PylonCanSnapshot(
        state_of_charge=None if soc is None else PylonStateOfCharge(soc_percent=soc, soh_percent=100),
        measurements=None if voltage is None else PylonMeasurements(voltage_v=voltage, current_a=0.0, temperature_c=20.0),
        extended_measurements=PylonExtendedMeasurements(
            min_cell_voltage_v=min_cell, max_cell_voltage_v=max_cell
        ),
    )


class ChargeCeilingTest(unittest.TestCase):
    def test_below_the_knee_has_no_ceiling(self) -> None:
        # 80% SOC, 53.0 V: below both knees -> CCL/budget governs, no cap.
        result = ChargeCeiling().evaluate(_battery(soc=80, voltage=53.0))
        self.assertIsNone(result.ceiling_a)
        self.assertEqual(result.reason, "below knee")

    def test_tapers_on_the_knee_taking_the_lower_of_soc_and_voltage(self) -> None:
        # 90% SOC ramp1 -> ~24 A; 54.6 V ramp2 -> ~7 A; min wins.
        result = ChargeCeiling().evaluate(_battery(soc=90, voltage=54.6))
        self.assertEqual(result.reason, "top-knee taper")
        self.assertAlmostEqual(result.ceiling_a, 7.0, delta=0.5)

    def test_high_cell_voltage_is_a_hard_stop(self) -> None:
        result = ChargeCeiling().evaluate(_battery(soc=90, voltage=54.0, max_cell=3.56, min_cell=3.40))
        self.assertEqual(result.ceiling_a, 0.0)
        self.assertIn("max cell", result.reason)

    def test_high_cell_delta_is_a_hard_stop(self) -> None:
        result = ChargeCeiling().evaluate(_battery(soc=90, voltage=54.0, max_cell=3.51, min_cell=3.30))
        self.assertEqual(result.ceiling_a, 0.0)
        self.assertIn("delta", result.reason)

    def test_full_charge_latch_holds_zero_until_pack_rests_low(self) -> None:
        ceiling = ChargeCeiling()
        # Hits 100% -> latched at zero.
        self.assertEqual(ceiling.evaluate(_battery(soc=100, voltage=55.0)).ceiling_a, 0.0)
        self.assertTrue(ceiling.full_latched)
        # Drops to 96% but still high voltage -> still latched.
        latched = ceiling.evaluate(_battery(soc=96, voltage=54.5))
        self.assertEqual(latched.ceiling_a, 0.0)
        self.assertEqual(latched.reason, "full-charge latch")
        # Rests below reset SOC and reset voltage -> latch clears, taper resumes.
        cleared = ceiling.evaluate(_battery(soc=96, voltage=53.9))
        self.assertFalse(ceiling.full_latched)
        self.assertNotEqual(cleared.reason, "full-charge latch")


if __name__ == "__main__":
    unittest.main()
