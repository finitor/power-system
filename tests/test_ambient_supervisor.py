from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.ambient import AmbientDs18b20Client, AmbientProbeDisconnected, AmbientTelemetry
from offgrid_power.canbus import CanFrame, decode_pylon_snapshot
from offgrid_power.supervisor import Supervisor
from offgrid_power.terminal_display import (
    CHANGED_DIGIT_END,
    CHANGED_DIGIT_START,
    DOWN_ARROW,
    UP_ARROW,
    highlight_changed_digits,
    render_snapshot,
)


class FakeClassicClient:
    def read(self):
        raise RuntimeError("not connected in test")


class FakeAmbientClient:
    def read(self) -> AmbientTelemetry:
        return AmbientTelemetry(
            temperature_c=21.5,
            humidity_percent=44.0,
            captured_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
        )


class FakeDisconnectedAmbientClient:
    def read(self) -> AmbientTelemetry:
        raise AmbientProbeDisconnected("not connected in test")


class FakeBatteryCanClient:
    def read(self):
        return decode_pylon_snapshot(
            [
                CanFrame(0x351, bytes.fromhex("4802D007D007C001")),
                CanFrame(0x355, bytes.fromhex("1E00640000000000")),
                CanFrame(0x356, bytes.fromhex("7914000071000000")),
                CanFrame(0x359, bytes.fromhex("0000000002504E00")),
                CanFrame(0x35C, bytes.fromhex("C000000000000000")),
                CanFrame(0x373, bytes.fromhex("CA0CCF0C1B011C01")),
            ]
        )


class AmbientSupervisorTest(unittest.TestCase):
    def test_supervisor_includes_ambient_reading(self) -> None:
        supervisor = Supervisor(
            classic=FakeClassicClient(),
            ambient=FakeAmbientClient(),
        )

        snapshot = supervisor.read_snapshot()

        self.assertIsNotNone(snapshot.ambient)
        self.assertEqual(snapshot.ambient.temperature_c, 21.5)
        self.assertEqual(snapshot.ambient.humidity_percent, 44.0)
        self.assertIn("Classic read failed", snapshot.errors[0])

    def test_terminal_display_renders_ambient_reading(self) -> None:
        supervisor = Supervisor(
            classic=FakeClassicClient(),
            ambient=FakeAmbientClient(),
        )

        rendered = render_snapshot(supervisor.read_snapshot())

        self.assertIn("Temperature Probes", rendered)
        self.assertIn("Sensor 0 ambient temp", rendered)
        self.assertIn("21.5C", rendered)
        self.assertIn("44.0%", rendered)

    def test_terminal_display_renders_disconnected_ambient_probe(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertIn("Sensor 0 ambient temp: disconnected", rendered)

    def test_terminal_display_renders_battery_can_reading(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=FakeBatteryCanClient()).read_snapshot()

        rendered = render_snapshot(snapshot)

        self.assertIn("Battery Bank", rendered)
        self.assertIn("52.41V", rendered)
        self.assertIn("SOC  30%", rendered)
        self.assertIn("SOH 100%", rendered)
        self.assertIn("charge 58.4V/200.0A", rendered)
        self.assertIn("charge yes  discharge yes", rendered)
        self.assertIn("protect none  alarms none", rendered)
        self.assertIn("3.274-3.279V", rendered)

    def test_supervisor_treats_disconnected_ambient_probe_as_non_error_state(self) -> None:
        snapshot = Supervisor(classic=None, ambient=FakeDisconnectedAmbientClient()).read_snapshot()

        self.assertTrue(snapshot.ok)
        self.assertIsNone(snapshot.ambient)
        self.assertEqual(snapshot.errors, [])

    def test_disconnected_ambient_probe_does_not_hide_classic_errors(self) -> None:
        snapshot = Supervisor(
            classic=FakeClassicClient(),
            ambient=FakeDisconnectedAmbientClient(),
        ).read_snapshot()

        self.assertFalse(snapshot.ok)
        self.assertIsNone(snapshot.ambient)
        self.assertEqual(len(snapshot.errors), 1)
        self.assertIn("Classic read failed", snapshot.errors[0])

    def test_terminal_display_highlights_changed_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Sensor 0 ambient temp:  21.5C",
            current="Sensor 0 ambient temp:  23.5C",
        )

        self.assertIn(f"{CHANGED_DIGIT_START}23.5C{CHANGED_DIGIT_END}", highlighted)
        self.assertIn("21.5", highlight_changed_digits(None, "21.5"))

    def test_terminal_display_still_highlights_time_digits_only(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Local time: 2026-05-28 15:48:49 EDT",
            current="Local time: 2026-05-28 15:48:55 EDT",
        )

        self.assertIn(f":{CHANGED_DIGIT_START}5{CHANGED_DIGIT_END}{CHANGED_DIGIT_START}5{CHANGED_DIGIT_END} EDT", highlighted)

    def test_terminal_display_adds_direction_arrows_to_changed_values(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Battery:  54.2V    3.6A    196W\nLocal time: 2026-05-28 15:48:49 EDT",
            current="Battery:  54.1V    3.8A    190W\nLocal time: 2026-05-28 15:48:55 EDT",
        )

        self.assertIn(DOWN_ARROW, highlighted)
        self.assertIn(UP_ARROW, highlighted)
        self.assertEqual(highlighted.count(UP_ARROW) + highlighted.count(DOWN_ARROW), 3)

    def test_terminal_display_pads_unchanged_value_arrow_slots(self) -> None:
        highlighted = highlight_changed_digits(
            previous="Battery:  54.2V    3.6A",
            current="Battery:  54.2V    3.8A",
        )

        self.assertIn("54.2V ", highlighted)
        self.assertIn(UP_ARROW, highlighted)

    def test_ds18b20_reads_sysfs_temperature(self) -> None:
        device_dir = REPO_ROOT / ".tmp-test-ds18b20" / "28-000001"
        device_dir.mkdir(parents=True, exist_ok=True)
        try:
            (device_dir / "w1_slave").write_text(
                "aa bb cc dd ee ff gg hh ii : crc=11 YES\n"
                "aa bb cc dd ee ff gg hh ii t=21562\n",
                encoding="utf-8",
            )

            telemetry = AmbientDs18b20Client(
                devices_path=str(device_dir.parent),
            ).read()

            self.assertEqual(telemetry.temperature_c, 21.562)
            self.assertIsNone(telemetry.humidity_percent)
        finally:
            (device_dir / "w1_slave").unlink(missing_ok=True)
            device_dir.rmdir()
            device_dir.parent.rmdir()

    def test_ds18b20_rejects_zero_temperature_as_disconnected(self) -> None:
        device_dir = REPO_ROOT / ".tmp-test-ds18b20-zero" / "28-000001"
        device_dir.mkdir(parents=True, exist_ok=True)
        try:
            (device_dir / "w1_slave").write_text(
                "aa bb cc dd ee ff gg hh ii : crc=11 YES\n"
                "aa bb cc dd ee ff gg hh ii t=0\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(AmbientProbeDisconnected, "probe may be disconnected"):
                AmbientDs18b20Client(
                    devices_path=str(device_dir.parent),
                ).read()
        finally:
            (device_dir / "w1_slave").unlink(missing_ok=True)
            device_dir.rmdir()
            device_dir.parent.rmdir()


if __name__ == "__main__":
    unittest.main()
