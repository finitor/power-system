from __future__ import annotations

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import PylonRequestFlags  # noqa: E402
from offgrid_power.charge_ceiling import ChargeEnableResolver  # noqa: E402


def _flags(charge_enable: bool) -> PylonRequestFlags:
    return PylonRequestFlags(
        charge_enable=charge_enable,
        discharge_enable=True,
        force_charge_1=False,
        force_charge_2=False,
        full_charge_request=False,
    )


class ChargeEnableResolverTest(unittest.TestCase):
    def test_present_flag_used_verbatim_enabled(self) -> None:
        resolver = ChargeEnableResolver()
        result = resolver.resolve(_flags(True), now=100.0)
        self.assertTrue(result.charge_enabled)
        self.assertFalse(result.degraded)

    def test_present_flag_genuine_stop_acts_immediately(self) -> None:
        # A real BMS charge-disable (frame present, bit clear) is honored at once,
        # not softened by the resolver.
        resolver = ChargeEnableResolver()
        result = resolver.resolve(_flags(False), now=100.0)
        self.assertFalse(result.charge_enabled)
        self.assertFalse(result.degraded)

    def test_single_dropped_frame_holds_last_known_enabled(self) -> None:
        resolver = ChargeEnableResolver(hold_s=45.0)
        resolver.resolve(_flags(True), now=100.0)
        # One missing frame a few seconds later must not read as a stop.
        result = resolver.resolve(None, now=110.0)
        self.assertTrue(result.charge_enabled)
        self.assertFalse(result.degraded)
        self.assertIn("hold", result.reason)

    def test_dropped_frame_holds_last_known_disabled(self) -> None:
        # If the BMS genuinely said stop, a subsequent dropped frame holds the stop
        # (does not spuriously re-enable) within the grace window.
        resolver = ChargeEnableResolver(hold_s=45.0)
        resolver.resolve(_flags(False), now=100.0)
        result = resolver.resolve(None, now=120.0)
        self.assertFalse(result.charge_enabled)
        self.assertFalse(result.degraded)

    def test_sustained_blindness_releases_to_controllers(self) -> None:
        resolver = ChargeEnableResolver(hold_s=45.0)
        resolver.resolve(_flags(True), now=100.0)
        result = resolver.resolve(None, now=200.0)  # 100s > 45s grace
        self.assertTrue(result.charge_enabled)
        self.assertTrue(result.degraded)
        self.assertIn("release", result.reason)

    def test_sustained_blindness_releases_even_if_last_known_was_stop(self) -> None:
        # The dangerous failure is a latched stop; once blind past the grace window
        # the resolver reverts to autonomous control regardless of last-known.
        resolver = ChargeEnableResolver(hold_s=45.0)
        resolver.resolve(_flags(False), now=100.0)
        result = resolver.resolve(None, now=200.0)
        self.assertTrue(result.charge_enabled)
        self.assertTrue(result.degraded)

    def test_cold_ambient_blocks_blind_release(self) -> None:
        resolver = ChargeEnableResolver(hold_s=45.0, cold_release_block_c=2.0)
        resolver.resolve(_flags(True), now=100.0)
        result = resolver.resolve(None, ambient_c=-3.0, now=200.0)
        self.assertFalse(result.charge_enabled)
        self.assertTrue(result.degraded)
        self.assertIn("hold off", result.reason)

    def test_warm_ambient_allows_blind_release(self) -> None:
        resolver = ChargeEnableResolver(hold_s=45.0, cold_release_block_c=2.0)
        resolver.resolve(_flags(True), now=100.0)
        result = resolver.resolve(None, ambient_c=15.0, now=200.0)
        self.assertTrue(result.charge_enabled)
        self.assertTrue(result.degraded)

    def test_cold_gate_uses_threshold_boundary(self) -> None:
        # At exactly the threshold, still treated as cold (<=), so hold off.
        resolver = ChargeEnableResolver(hold_s=45.0, cold_release_block_c=2.0)
        resolver.resolve(_flags(True), now=100.0)
        result = resolver.resolve(None, ambient_c=2.0, now=200.0)
        self.assertFalse(result.charge_enabled)

    def test_cold_ambient_within_grace_still_holds_last_known(self) -> None:
        # Cold gate only applies once blindness is sustained; a single cold-weather
        # frame gap still just holds last-known (enabled), not disabled.
        resolver = ChargeEnableResolver(hold_s=45.0, cold_release_block_c=2.0)
        resolver.resolve(_flags(True), now=100.0)
        result = resolver.resolve(None, ambient_c=-10.0, now=110.0)
        self.assertTrue(result.charge_enabled)
        self.assertFalse(result.degraded)

    def test_recovery_clears_degraded(self) -> None:
        resolver = ChargeEnableResolver(hold_s=45.0)
        resolver.resolve(_flags(True), now=100.0)
        blind = resolver.resolve(None, now=200.0)
        self.assertTrue(blind.degraded)
        recovered = resolver.resolve(_flags(True), now=205.0)
        self.assertFalse(recovered.degraded)
        # And the grace clock is re-seeded, so the next single gap holds again.
        held = resolver.resolve(None, now=210.0)
        self.assertFalse(held.degraded)

    def test_cold_start_no_flag_ever_releases_when_warm(self) -> None:
        # Never having seen a BMS command, a missing frame degrades to release
        # (safe: controllers self-regulate; ceiling safety stops still run).
        resolver = ChargeEnableResolver(hold_s=45.0)
        result = resolver.resolve(None, ambient_c=18.0, now=50.0)
        self.assertTrue(result.charge_enabled)
        self.assertTrue(result.degraded)


if __name__ == "__main__":
    unittest.main()
