from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from dataclasses import replace

from offgrid_power.magnum import (
    InverterEventTracker,
    MagnumClient,
    MagnumSnapshot,
    _find_packets,
    _snapshot_from_cycle,
    decode_remote_lbco_v,
)


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


class LbcoDecodeTest(unittest.TestCase):
    def test_decodes_live_48v_wire_value_without_normal_4x_multiplier(self) -> None:
        # Remote byte 9 is 0xF0. Magnum's 48V LBCO field uses the 24V wire
        # encoding, so this is 48.0V; the generic 4x decoder would say 96.0V.
        self.assertEqual(decode_remote_lbco_v(REMOTE), 48.0)

    def test_rejects_short_packet(self) -> None:
        with self.assertRaisesRegex(ValueError, "too short"):
            decode_remote_lbco_v(bytes(9))

    def test_rejects_implausible_value(self) -> None:
        packet = bytearray(REMOTE)
        packet[9] = 100
        with self.assertRaisesRegex(ValueError, "Implausible"):
            decode_remote_lbco_v(bytes(packet))


def _magnum(inverter_on: bool, fault: str = "NONE", dc_volts: float = 53.0) -> MagnumSnapshot:
    from datetime import datetime, timezone
    return MagnumSnapshot(
        captured_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        dc_volts=dc_volts, dc_amps=0, ac_volts_out=120, ac_volts_in=0,
        ac_amps_in=0, ac_amps_out=0, ac_freq_hz=60.0,
        inverter_on=inverter_on, charger_on=False,
        status_name="INVERT" if inverter_on else "OFF", fault_name=fault,
        battery_temp_c=25, transformer_temp_c=30, fet_temp_c=30,
    )


class InverterEventTrackerTest(unittest.TestCase):
    def test_no_event_on_first_observation_or_steady_state(self) -> None:
        t = InverterEventTracker()
        self.assertIsNone(t.observe(_magnum(True)))
        self.assertIsNone(t.observe(_magnum(True)))

    def test_low_battery_off_transition_is_lbco_cutout(self) -> None:
        t = InverterEventTracker()
        t.observe(_magnum(True))
        event = t.observe(_magnum(False, fault="LOW_BAT", dc_volts=47.8))
        self.assertEqual(event.source, "magnum")
        self.assertEqual(event.event, "lbco_cutout")
        self.assertEqual(event.detail["fault"], "LOW_BAT")
        self.assertEqual(event.detail["dc_volts"], 47.8)

    def test_plain_off_transition_when_no_low_battery_fault(self) -> None:
        t = InverterEventTracker()
        t.observe(_magnum(True))
        event = t.observe(_magnum(False, fault="NONE"))
        self.assertEqual(event.event, "inverter_off")

    def test_on_transition_logged(self) -> None:
        t = InverterEventTracker()
        t.observe(_magnum(False, fault="LOW_BAT"))
        event = t.observe(_magnum(True))
        self.assertEqual(event.event, "inverter_on")

    def test_none_snapshot_is_ignored(self) -> None:
        t = InverterEventTracker()
        t.observe(_magnum(True))
        self.assertIsNone(t.observe(None))


class MagnumClientTest(unittest.TestCase):
    def test_read_raises_when_serial_device_is_absent(self) -> None:
        # An unplugged adapter (no serial node) is surfaced as an error so the
        # supervisor can classify it as transport_absent, not swallowed as None.
        client = MagnumClient("/dev/definitely-not-a-real-magnum-adapter")

        with self.assertRaises(ConnectionError) as ctx:
            client.read()

        self.assertIn("Could not open", str(ctx.exception))


class MagnumLastSettingsTest(unittest.TestCase):
    """The remote packet (charge settings) isn't in every cycle; the client
    fills missing settings from the last seen values so the display doesn't
    strobe."""

    @staticmethod
    def _client() -> MagnumClient:
        return MagnumClient("/dev/unused-for-merge-test")

    def test_missing_settings_filled_from_confirmed_last_seen(self) -> None:
        client = self._client()
        full = replace(
            _magnum(True),
            absorb_v=58.0, float_v=54.0, absorb_time_hr=2.0,
            shore_amps=30, charger_amps_pct=80,
        )
        # First cycle is only a candidate; a one-off bad remote decode should
        # not make static settings flash on the display.
        first = client._merge_last_settings(full)
        self.assertIsNone(first.absorb_v)
        # Repeating the same tuple confirms it.
        confirmed = client._merge_last_settings(full)
        self.assertEqual(confirmed.absorb_v, 58.0)
        # Next cycle has no remote packet -> fields filled from cache.
        merged = client._merge_last_settings(_magnum(True, dc_volts=52.1))
        self.assertEqual(merged.absorb_v, 58.0)
        self.assertEqual(merged.float_v, 54.0)
        self.assertEqual(merged.absorb_time_hr, 2.0)
        self.assertEqual(merged.shore_amps, 30)
        self.assertEqual(merged.charger_amps_pct, 80)
        # Non-settings fields are this cycle's, untouched.
        self.assertEqual(merged.dc_volts, 52.1)

    def test_fresh_settings_update_the_cache(self) -> None:
        client = self._client()
        client._merge_last_settings(replace(_magnum(True), absorb_v=58.0))
        client._merge_last_settings(replace(_magnum(True), absorb_v=58.0))
        # A single different decode is held as pending, not displayed.
        pending = client._merge_last_settings(replace(_magnum(True), absorb_v=59.5))
        self.assertEqual(pending.absorb_v, 58.0)
        # Repeating the decode confirms the changed static setting.
        client._merge_last_settings(replace(_magnum(True), absorb_v=59.5))
        merged = client._merge_last_settings(_magnum(True))
        self.assertEqual(merged.absorb_v, 59.5)

    def test_no_cache_leaves_none(self) -> None:
        client = self._client()
        merged = client._merge_last_settings(_magnum(True))
        self.assertIsNone(merged.absorb_v)
        self.assertIsNone(merged.float_v)

    def test_one_off_limit_decode_does_not_flash_on(self) -> None:
        client = self._client()
        client._merge_last_settings(replace(_magnum(True), charger_amps_pct=0))
        client._merge_last_settings(replace(_magnum(True), charger_amps_pct=0))

        merged = client._merge_last_settings(replace(_magnum(True), charger_amps_pct=80))

        self.assertEqual(merged.charger_amps_pct, 0)


if __name__ == "__main__":
    unittest.main()
