from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.ambient import AmbientDs18b20Client, AmbientTelemetry
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


if __name__ == "__main__":
    unittest.main()
