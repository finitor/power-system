from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import CanFrame
from offgrid_power.cli.can_survey import candump_line, parse_bitrates, pgn_from_arbitration_id, summarize_frames


class CanSurveyTest(unittest.TestCase):
    def test_parse_bitrates_accepts_comma_separated_values(self) -> None:
        self.assertEqual(parse_bitrates("500000, 250000"), [500000, 250000])

    def test_summarize_frames_counts_standard_and_extended_ids(self) -> None:
        summary = summarize_frames(
            [
                CanFrame(0x351, b"\x01", 1.0),
                CanFrame(0x351, b"\x02", 2.0),
                CanFrame(0x09F8017F, b"\x03", 3.0, is_extended_id=True),
            ]
        )

        self.assertEqual(summary.frame_count, 3)
        self.assertEqual(summary.unique_ids, (0x351, 0x09F8017F))
        self.assertEqual(summary.standard_count, 2)
        self.assertEqual(summary.extended_count, 1)
        self.assertEqual(summary.top_ids[0], (0x351, 2))
        self.assertEqual(summary.top_pgns, ((0x1F801, 1),))

    def test_pgn_from_arbitration_id_zeros_destination_for_pdu1(self) -> None:
        self.assertEqual(pgn_from_arbitration_id(0x18EAFF7F), 0x0EA00)

    def test_candump_line_renders_uppercase_hex(self) -> None:
        self.assertEqual(
            candump_line("can0", CanFrame(0x351, bytes.fromhex("0102"), 1780064750.959438)),
            "(1780064750.959438) can0 351#0102\n",
        )


if __name__ == "__main__":
    unittest.main()
