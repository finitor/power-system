import unittest

from offgrid_power.charge_stage import (
    ChargeStage,
    NormalizedStage,
    classic_stage,
    epever_stage,
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


class NormalizedStageTest(unittest.TestCase):
    def test_vendor_present_only_when_it_differs(self) -> None:
        # EPEver Boost differs from canonical Absorb -> vendor carried.
        self.assertEqual(epever_stage("Boost"), NormalizedStage("Absorb", "Boost"))
        # EPEver Float == canonical Float -> no vendor noise.
        self.assertEqual(epever_stage("Float"), NormalizedStage("Float", None))
        # Classic BulkMppt -> canonical Bulk, native carried.
        self.assertEqual(classic_stage("BulkMppt"), NormalizedStage("Bulk", "BulkMppt"))

    def test_render_without_state_register_keeps_native_nuance(self) -> None:
        # EPEver has no state register: the native word is the only extra signal.
        self.assertEqual(NormalizedStage("Absorb", "Boost").render(), "Absorb (Boost)")
        self.assertEqual(NormalizedStage("Float", None).render(), "Float")

    def test_render_fuses_phase_and_activity_into_dense_token(self) -> None:
        # Classic: phase already implies the converter activity -> just the phase,
        # and the native word (BulkMppt) is dropped as redundant.
        self.assertEqual(
            NormalizedStage("Bulk", "BulkMppt").render("MPPT or regulating voltage"),
            "Bulk",
        )
        self.assertEqual(
            NormalizedStage("Float", "FloatMppt").render("MPPT or regulating voltage"),
            "Float",
        )
        # Resting inside a charging phase is the one case worth distinguishing:
        # at the float target but not converting.
        self.assertEqual(
            NormalizedStage("Float", "FloatMppt").render("Resting"), "Float (idle)"
        )
        # Waking and HyperVoc collapse to single tokens; plain Resting stays Resting.
        self.assertEqual(NormalizedStage("Bulk", None).render("Waking / Starting"), "Waking")
        self.assertEqual(NormalizedStage("HyperVoc", None).render("Resting"), "HyperVoc")
        self.assertEqual(NormalizedStage("Resting", None).render("Resting"), "Resting")

    def test_dict_roundtrip(self) -> None:
        pair = epever_stage("Boost")
        self.assertEqual(pair.as_dict(), {"canonical": "Absorb", "vendor": "Boost"})
        self.assertEqual(NormalizedStage.from_dict(pair.as_dict()), pair)
        # Missing/empty block degrades to Unknown rather than raising.
        self.assertEqual(NormalizedStage.from_dict(None).canonical, ChargeStage.UNKNOWN.value)


if __name__ == "__main__":
    unittest.main()
