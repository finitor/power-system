from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "software" / "pi-controller" / "src"))

from offgrid_power.runtime_state import (  # noqa: E402
    CCL_SCALING_FACTOR_KEY,
    CHARGE_CONTROLLER_ENABLED_KEY,
    load_ccl_scaling_factor,
    load_charge_controller_enabled,
    save_ccl_scaling_factor,
    save_charge_controller_enabled,
)


class RuntimeStateTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "runtime-state.json"

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_roundtrip(self) -> None:
        save_ccl_scaling_factor(self.path, 0.65)
        self.assertEqual(load_ccl_scaling_factor(self.path), 0.65)

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(load_ccl_scaling_factor(self.path))

    def test_disabled_path_is_noop(self) -> None:
        self.assertIsNone(load_ccl_scaling_factor(None))
        save_ccl_scaling_factor(None, 0.6)  # must not raise

    def test_out_of_range_value_ignored(self) -> None:
        self.path.write_text(json.dumps({CCL_SCALING_FACTOR_KEY: 1.5}), encoding="utf-8")
        self.assertIsNone(load_ccl_scaling_factor(self.path))

    def test_corrupt_file_ignored(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(load_ccl_scaling_factor(self.path))

    def test_missing_key_ignored(self) -> None:
        self.path.write_text(json.dumps({"something_else": 1}), encoding="utf-8")
        self.assertIsNone(load_ccl_scaling_factor(self.path))

    def test_controller_switches_roundtrip_and_preserve_scaling(self) -> None:
        save_ccl_scaling_factor(self.path, 0.65)
        save_charge_controller_enabled(self.path, {0: True, 1: False})

        self.assertEqual(load_charge_controller_enabled(self.path), {0: True, 1: False})
        self.assertEqual(load_ccl_scaling_factor(self.path), 0.65)

        save_ccl_scaling_factor(self.path, 0.55)
        self.assertEqual(load_charge_controller_enabled(self.path), {0: True, 1: False})

    def test_controller_switches_ignore_non_boolean_values(self) -> None:
        self.path.write_text(
            json.dumps({CHARGE_CONTROLLER_ENABLED_KEY: {"0": False, "1": "false"}}),
            encoding="utf-8",
        )
        self.assertEqual(load_charge_controller_enabled(self.path), {0: False})


if __name__ == "__main__":
    unittest.main()
