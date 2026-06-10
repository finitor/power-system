from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.magnum import _find_packets, _snapshot_from_cycle


# Captured live from the MS4448PAE bus on 2026-06-09. The magnum-pi
# CycleTracker labelled these exactly backwards after joining the bus
# mid-cycle (the remote's byte 10 is non-zero, so it was fingerprinted as
# the inverter), which produced 2573.7V / 137C decodes. These bytes are the
# regression fixture for our model-byte identification.
INVERTER_HEX = "400002140004780001003D19251E73000001025800FF"
REMOTE_HEX = "00056489001E1C0600F09B89001E142B0000170000FF"

INVERTER = bytes.fromhex(INVERTER_HEX)
REMOTE = bytes.fromhex(REMOTE_HEX)


class FindPacketsTest(unittest.TestCase):
    def test_identifies_packets_regardless_of_cycle_order(self) -> None:
        for order in ([INVERTER, REMOTE], [REMOTE, INVERTER]):
            raw_packets = [(None, data) for data in order]
            inverter, remote = _find_packets(raw_packets)
            self.assertEqual(inverter, INVERTER)
            self.assertEqual(remote, REMOTE)

    def test_ignores_accessory_packets(self) -> None:
        bmk = bytes([0x81] + [0] * 20)
        raw_packets = [(None, bmk), (None, REMOTE), (None, INVERTER)]
        inverter, remote = _find_packets(raw_packets)
        self.assertEqual(inverter, INVERTER)
        self.assertEqual(remote, REMOTE)

    def test_no_inverter_when_model_byte_absent(self) -> None:
        inverter, remote = _find_packets([(None, REMOTE)])
        self.assertIsNone(inverter)

    def test_short_packets_are_skipped(self) -> None:
        inverter, remote = _find_packets([(None, b"\x91\x00"), (None, INVERTER)])
        self.assertEqual(inverter, INVERTER)
        self.assertIsNone(remote)


class SnapshotFromCycleTest(unittest.TestCase):
    def test_decodes_inverter_fields(self) -> None:
        snapshot = _snapshot_from_cycle([(None, INVERTER), (None, REMOTE)])

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.status_name, "INVERT")
        self.assertEqual(snapshot.fault_name, "NONE")
        self.assertAlmostEqual(snapshot.dc_volts, 53.2)
        self.assertEqual(snapshot.dc_amps, 4)
        self.assertEqual(snapshot.ac_volts_out, 120)
        self.assertEqual(snapshot.ac_volts_in, 0)
        self.assertEqual(snapshot.ac_amps_out, 1)
        self.assertEqual(snapshot.ac_amps_in, 0)
        self.assertAlmostEqual(snapshot.ac_freq_hz, 60.0)
        self.assertTrue(snapshot.inverter_on)
        self.assertFalse(snapshot.charger_on)
        self.assertEqual(snapshot.battery_temp_c, 25)
        self.assertEqual(snapshot.transformer_temp_c, 37)
        self.assertEqual(snapshot.fet_temp_c, 30)
        self.assertEqual(snapshot.dc_power_w, 213)
        self.assertEqual(snapshot.status_label(), "Inverting")
        self.assertIsNone(snapshot.fault_label())

    def test_decodes_remote_settings_with_48v_scaling(self) -> None:
        snapshot = _snapshot_from_cycle([(None, INVERTER), (None, REMOTE)])

        # Wire bytes are 12V-nominal x10; 0x89 = 137 -> 54.8V at 4x.
        self.assertAlmostEqual(snapshot.absorb_v, 54.8)
        self.assertAlmostEqual(snapshot.float_v, 54.8)
        self.assertAlmostEqual(snapshot.absorb_time_hr, 3.0)
        self.assertEqual(snapshot.shore_amps, 30)
        self.assertEqual(snapshot.charger_amps_pct, 0)

    def test_decodes_with_packets_in_mislabelled_order(self) -> None:
        # The order magnum-pi actually delivered them on 2026-06-09.
        snapshot = _snapshot_from_cycle([(None, REMOTE), (None, INVERTER)])

        self.assertIsNotNone(snapshot)
        self.assertAlmostEqual(snapshot.dc_volts, 53.2)
        self.assertAlmostEqual(snapshot.float_v, 54.8)

    def test_returns_none_without_identifiable_inverter(self) -> None:
        self.assertIsNone(_snapshot_from_cycle([(None, REMOTE)]))
        self.assertIsNone(_snapshot_from_cycle([]))

    def test_inverter_only_cycle_yields_snapshot_without_settings(self) -> None:
        snapshot = _snapshot_from_cycle([(None, INVERTER)])

        self.assertIsNotNone(snapshot)
        self.assertAlmostEqual(snapshot.dc_volts, 53.2)
        self.assertIsNone(snapshot.absorb_v)
        self.assertIsNone(snapshot.float_v)


if __name__ == "__main__":
    unittest.main()
