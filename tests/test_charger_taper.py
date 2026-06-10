from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import (  # noqa: E402
    PylonCanSnapshot,
    PylonChargeLimits,
    PylonExtendedMeasurements,
    PylonMeasurements,
    PylonRequestFlags,
    PylonStateOfCharge,
)
from offgrid_power.charger_taper import (  # noqa: E402
    append_decision_log,
    ChargerCurrentSettings,
    ChargerCurrentTaperController,
    ChargerTelemetry,
)


class ChargerTaperTest(unittest.TestCase):
    def test_uses_bulk_current_below_top_knee(self) -> None:
        decision = ChargerCurrentTaperController().decide(
            self._charger(voltage_v=52.9, stage="BulkMppt"),
            self._settings(current_a=40.0),
            self._battery(soc=80, voltage_v=52.9, ccl_a=200.0),
        )

        self.assertEqual(decision.target_current_a, 80.0)
        self.assertTrue(decision.should_write)

    def test_never_targets_above_operator_ceiling(self) -> None:
        # Bulk must restore to the panel-configured limit, never beyond it,
        # regardless of BMS CCL headroom.
        decision = ChargerCurrentTaperController().decide(
            self._charger(voltage_v=52.0, stage="BulkMppt"),
            self._settings(current_a=80.0),
            self._battery(soc=60, voltage_v=52.0, ccl_a=200.0),
        )

        self.assertEqual(decision.target_current_a, 80.0)
        self.assertFalse(decision.should_write)

    def test_ramps_down_from_soc_or_voltage_whichever_is_lower(self) -> None:
        decision = ChargerCurrentTaperController().decide(
            self._charger(voltage_v=54.5),
            self._settings(current_a=80.0),
            self._battery(soc=90, voltage_v=54.5, ccl_a=200.0),
        )

        self.assertAlmostEqual(decision.target_current_a, 8.5)

    def test_clamps_to_bms_ccl(self) -> None:
        decision = ChargerCurrentTaperController().decide(
            self._charger(voltage_v=53.0, stage="BulkMppt"),
            self._settings(current_a=80.0),
            self._battery(soc=80, voltage_v=53.0, ccl_a=40.0),
        )

        self.assertEqual(decision.target_current_a, 40.0)

    def test_stops_when_bms_disables_charge(self) -> None:
        decision = ChargerCurrentTaperController().decide(
            self._charger(voltage_v=54.7),
            self._settings(current_a=20.0),
            self._battery(soc=96, voltage_v=54.7, ccl_a=0.0, charge_enable=False),
        )

        self.assertEqual(decision.target_current_a, 0.0)
        self.assertEqual(decision.reason, "BMS charge disabled")

    def test_stops_on_high_cell_voltage(self) -> None:
        decision = ChargerCurrentTaperController().decide(
            self._charger(voltage_v=55.0),
            self._settings(current_a=20.0),
            self._battery(soc=94, voltage_v=55.0, min_cell_v=3.40, max_cell_v=3.55),
        )

        self.assertEqual(decision.target_current_a, 0.0)
        self.assertIn("max cell", decision.reason)

    def test_full_charge_latch_holds_zero_until_pack_rests(self) -> None:
        controller = ChargerCurrentTaperController()
        first = controller.decide(
            self._charger(voltage_v=54.7),
            self._settings(current_a=20.0),
            self._battery(soc=100, voltage_v=54.7),
        )
        second = controller.decide(
            self._charger(voltage_v=54.2),
            self._settings(current_a=20.0),
            self._battery(soc=99, voltage_v=54.2),
        )
        third = controller.decide(
            self._charger(voltage_v=53.9),
            self._settings(current_a=20.0),
            self._battery(soc=97, voltage_v=53.9),
        )

        self.assertEqual(first.target_current_a, 0.0)
        self.assertEqual(second.target_current_a, 0.0)
        self.assertGreater(third.target_current_a or 0.0, 0.0)

    def test_ignores_resting_charger_stage_unless_safety_stop_applies(self) -> None:
        decision = ChargerCurrentTaperController().decide(
            self._charger(voltage_v=53.0, stage="Resting"),
            self._settings(current_a=40.0),
            self._battery(soc=80, voltage_v=53.0),
        )

        self.assertIsNone(decision.target_current_a)

    def _charger(self, voltage_v: float, stage: str = "Absorb") -> ChargerTelemetry:
        return ChargerTelemetry(voltage_v=voltage_v, charge_stage=stage)

    def _settings(self, current_a: float) -> ChargerCurrentSettings:
        return ChargerCurrentSettings(current_limit_a=current_a)

    def _battery(
        self,
        *,
        soc: int,
        voltage_v: float,
        ccl_a: float = 200.0,
        charge_enable: bool = True,
        min_cell_v: float = 3.30,
        max_cell_v: float = 3.35,
    ) -> PylonCanSnapshot:
        return PylonCanSnapshot(
            charge_limits=PylonChargeLimits(
                charge_voltage_limit_v=58.4,
                charge_current_limit_a=ccl_a,
                discharge_current_limit_a=200.0,
                discharge_voltage_limit_v=44.8,
            ),
            state_of_charge=PylonStateOfCharge(soc_percent=soc, soh_percent=100),
            measurements=PylonMeasurements(voltage_v=voltage_v, current_a=0.0, temperature_c=20.0),
            request_flags=PylonRequestFlags(
                charge_enable=charge_enable,
                discharge_enable=True,
                force_charge_1=False,
                force_charge_2=False,
                full_charge_request=False,
            ),
            extended_measurements=PylonExtendedMeasurements(
                min_cell_voltage_v=min_cell_v,
                max_cell_voltage_v=max_cell_v,
            ),
        )


class DecisionLogTest(unittest.TestCase):
    def test_appends_header_and_row(self) -> None:
        from offgrid_power.charger_taper import ChargerCurrentTaperDecision

        log_path = Path(__file__).resolve().parents[1] / ".tmp-test-taper-log.csv"
        log_path.unlink(missing_ok=True)
        try:
            decision = ChargerCurrentTaperDecision(20.0, "dynamic taper", should_write=True)
            append_decision_log(
                str(log_path),
                dry_run=True,
                charge_stage="Absorb",
                battery_voltage_v=54.21,
                current_limit_a=80.0,
                decision=decision,
                battery=None,
            )
            rows = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(rows[0].split(",")[0:3], ["captured_at", "mode", "charge_stage"])
            self.assertIn("dry-run,Absorb,54.21,80.0,20.0,dynamic taper", rows[1])
        finally:
            log_path.unlink(missing_ok=True)

    def test_empty_path_is_a_noop(self) -> None:
        from offgrid_power.charger_taper import ChargerCurrentTaperDecision

        append_decision_log(
            "",
            dry_run=True,
            charge_stage=None,
            battery_voltage_v=None,
            current_limit_a=None,
            decision=ChargerCurrentTaperDecision(None, "x"),
            battery=None,
        )


if __name__ == "__main__":
    unittest.main()
