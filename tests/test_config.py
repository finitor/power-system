import os
import unittest
from unittest.mock import patch

from offgrid_power.config import load_config


class ConfigTest(unittest.TestCase):
    def test_magnum_stale_threshold_defaults_to_reader_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(load_config().display.magnum_stale_after_seconds)

    def test_magnum_stale_threshold_can_be_overridden(self) -> None:
        with patch.dict(os.environ, {"MAGNUM_STALE_AFTER_SECONDS": "45"}, clear=True):
            self.assertEqual(load_config().display.magnum_stale_after_seconds, 45.0)


if __name__ == "__main__":
    unittest.main()
