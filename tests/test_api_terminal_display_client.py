from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.cli.api_terminal_display import (
    VIEW_POWER,
    VIEW_WEATHER,
    compose_frame,
    derive_weather_url,
    footer,
    resolve_key,
)


class DeriveWeatherUrlTest(unittest.TestCase):
    def test_swaps_snapshot_path_for_weather(self) -> None:
        self.assertEqual(
            derive_weather_url("http://127.0.0.1:8081/api/v1/snapshot"),
            "http://127.0.0.1:8081/api/v1/weather",
        )

    def test_leaves_unrecognized_url_untouched(self) -> None:
        self.assertEqual(derive_weather_url("http://host/custom"), "http://host/custom")


class ResolveKeyTest(unittest.TestCase):
    def test_explicit_view_keys(self) -> None:
        self.assertEqual(resolve_key("p", VIEW_WEATHER), VIEW_POWER)
        self.assertEqual(resolve_key("w", VIEW_POWER), VIEW_WEATHER)
        self.assertEqual(resolve_key("W", VIEW_POWER), VIEW_WEATHER)  # case-insensitive

    def test_space_and_tab_toggle(self) -> None:
        self.assertEqual(resolve_key(" ", VIEW_POWER), VIEW_WEATHER)
        self.assertEqual(resolve_key(" ", VIEW_WEATHER), VIEW_POWER)
        self.assertEqual(resolve_key("\t", VIEW_POWER), VIEW_WEATHER)

    def test_quit_key(self) -> None:
        self.assertEqual(resolve_key("q", VIEW_POWER), "quit")
        self.assertEqual(resolve_key("Q", VIEW_WEATHER), "quit")

    def test_unrecognized_key_ignored(self) -> None:
        self.assertIsNone(resolve_key("x", VIEW_POWER))
        self.assertIsNone(resolve_key("\n", VIEW_POWER))


class FooterTest(unittest.TestCase):
    def test_marks_the_active_view(self) -> None:
        power = footer(VIEW_POWER)
        self.assertIn("[p] POWER", power)
        self.assertIn("[w] Weather", power)
        self.assertIn("[q] Quit", power)

        weather = footer(VIEW_WEATHER)
        self.assertIn("[w] WEATHER", weather)
        self.assertIn("[p] Power", weather)


class ComposeFrameTest(unittest.TestCase):
    def test_pins_footer_to_bottom_row(self) -> None:
        frame = compose_frame("line1\nline2", "FOOTER", height=6)
        rows = frame.split("\n")

        self.assertEqual(len(rows), 6)  # exactly fills the pane height
        self.assertEqual(rows[0], "line1")
        self.assertEqual(rows[1], "line2")
        self.assertEqual(rows[2:5], ["", "", ""])  # blank gap
        self.assertEqual(rows[-1], "FOOTER")
        self.assertFalse(frame.endswith("\n"))  # no trailing newline -> no scroll

    def test_truncates_body_taller_than_pane(self) -> None:
        body = "\n".join(f"line{n}" for n in range(10))
        frame = compose_frame(body, "FOOTER", height=4)
        rows = frame.split("\n")

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1], "FOOTER")
        self.assertEqual(rows[:3], ["line0", "line1", "line2"])


if __name__ == "__main__":
    unittest.main()
