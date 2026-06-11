import unittest

from offgrid_power.charge_stage import (
    ChargeStage,
    normalize_classic_stage,
    normalize_epever_stage,
)


class ChargeStageTest(unittest.TestCase):
    def test_classic_native_words_map_to_canonical(self) -> None:
        cases = {
            "Resting": ChargeStage.RESTING,
            "BulkMppt": ChargeStage.BULK,
            "Absorb": ChargeStage.ABSORB,
            "Float": ChargeStage.FLOAT,
            "FloatMppt": ChargeStage.FLOAT,
            "Equalize": ChargeStage.EQUALIZE,
            "HyperVoc": ChargeStage.HYPERVOC,
        }
        for native, canonical in cases.items():
            self.assertEqual(normalize_classic_stage(native), canonical, native)

    def test_epever_native_words_map_to_canonical(self) -> None:
        cases = {
            "No charging": ChargeStage.RESTING,
            "Boost": ChargeStage.ABSORB,
            "Float": ChargeStage.FLOAT,
            "Equalize": ChargeStage.EQUALIZE,
        }
        for native, canonical in cases.items():
            self.assertEqual(normalize_epever_stage(native), canonical, native)

    def test_epever_boost_aligns_with_classic_absorb(self) -> None:
        # The two controllers' constant-voltage stages share a canonical value
        # so cross-controller coordination can compare them directly.
        self.assertEqual(
            normalize_epever_stage("Boost"),
            normalize_classic_stage("Absorb"),
        )

    def test_hypervoc_is_distinct_from_resting(self) -> None:
        # Protection state, observable, and supported by only one vendor.
        self.assertEqual(normalize_classic_stage("HyperVoc"), ChargeStage.HYPERVOC)
        self.assertNotEqual(ChargeStage.HYPERVOC, ChargeStage.RESTING)
        # The EPEver has no such state; its map never emits HYPERVOC.
        self.assertNotIn(
            ChargeStage.HYPERVOC,
            {normalize_epever_stage(s) for s in ("No charging", "Boost", "Float", "Equalize")},
        )

    def test_unknown_and_none_are_unknown(self) -> None:
        self.assertEqual(normalize_classic_stage("Wat"), ChargeStage.UNKNOWN)
        self.assertEqual(normalize_epever_stage(None), ChargeStage.UNKNOWN)

    def test_canonical_renders_as_plain_word(self) -> None:
        self.assertEqual(ChargeStage.ABSORB.value, "Absorb")
        self.assertEqual(f"{ChargeStage.BULK.value}", "Bulk")


if __name__ == "__main__":
    unittest.main()
