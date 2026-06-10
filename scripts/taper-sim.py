#!/usr/bin/env python3
"""Sweep a simulated charge day through the real taper controller.

Validates ChargerCurrentTaperController behavior without waiting for real
sun: drives SOC from 60% to 100% with plausible pack/cell voltages, then a
post-full dip, printing every decision change. Also exercises the safety
stops. Run anywhere the package imports:

    PYTHONPATH=software/pi-controller/src python3 scripts/taper-sim.py
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "software" / "pi-controller" / "src"))

from offgrid_power.canbus import (
    PylonCanSnapshot,
    PylonChargeLimits,
    PylonExtendedMeasurements,
    PylonMeasurements,
    PylonRequestFlags,
    PylonStateOfCharge,
)
from offgrid_power.charger_taper import (
    ChargerCurrentSettings,
    ChargerCurrentTaperController,
    ChargerTelemetry,
)


def battery(soc: float, voltage: float, max_cell: float, min_cell: float, charge_enable: bool = True, ccl: float = 200.0):
    return PylonCanSnapshot(
        state_of_charge=PylonStateOfCharge(soc_percent=int(soc), soh_percent=100),
        measurements=PylonMeasurements(voltage_v=voltage, current_a=10.0, temperature_c=20.0),
        charge_limits=PylonChargeLimits(
            charge_voltage_limit_v=58.4,
            charge_current_limit_a=ccl,
            discharge_current_limit_a=200.0,
            discharge_voltage_limit_v=44.8,
        ),
        request_flags=PylonRequestFlags(
            charge_enable=charge_enable,
            discharge_enable=True,
            force_charge_1=False,
            force_charge_2=False,
            full_charge_request=False,
        ),
        extended_measurements=PylonExtendedMeasurements(
            min_cell_voltage_v=min_cell,
            max_cell_voltage_v=max_cell,
        ),
    )


def charge_day_profile():
    """(label, soc, pack_v, max_cell_v, min_cell_v) — rising charge then post-full dip."""
    steps = []
    # SOC 60 -> 100: pack voltage rises 53.2 -> 55.0, cells 3.33 -> 3.47,
    # delta widens near the top as real packs do.
    for soc in range(60, 101):
        frac = (soc - 60) / 40
        pack_v = 53.2 + 1.8 * frac**2
        max_cell = 3.330 + 0.140 * frac**2 + (0.02 * frac**4)
        min_cell = 3.330 + 0.120 * frac**2
        steps.append((f"charging soc={soc}", soc, pack_v, max_cell, min_cell))
    # Hold full briefly, then evening discharge to reset window
    steps.append(("holding full", 100, 54.6, 3.45, 3.40))
    for soc, pack_v in [(99, 54.2), (98, 54.0), (97, 53.9), (95, 53.6)]:
        steps.append((f"discharging soc={soc}", soc, pack_v, 3.36, 3.34))
    # Next-morning recharge after the latch should have reset
    for soc in (96, 97, 98):
        steps.append((f"recharge soc={soc}", soc, 54.1, 3.41, 3.39))
    return steps


def main() -> int:
    controller = ChargerCurrentTaperController()
    settings_limit = 80.0
    print(f"start: charger limit {settings_limit:.0f}A, stage Absorb, BMS CCL 200A")
    last_target = None
    for label, soc, pack_v, max_cell, min_cell in charge_day_profile():
        decision = controller.decide(
            ChargerTelemetry(voltage_v=pack_v, charge_stage="Absorb"),
            ChargerCurrentSettings(current_limit_a=settings_limit),
            battery(soc, pack_v, max_cell, min_cell),
        )
        if decision.target_current_a != last_target:
            mark = "WRITE" if decision.should_write else "     "
            print(
                f"{mark} {label:20s} pack={pack_v:.2f}V cell={max_cell:.3f}/{min_cell:.3f} "
                f"-> target {decision.target_current_a}A ({decision.reason})"
            )
            last_target = decision.target_current_a
            if decision.should_write:
                settings_limit = decision.target_current_a

    print("\nsafety stops:")
    scenarios = [
        ("BMS charge disabled", battery(90, 54.0, 3.40, 3.38, charge_enable=False)),
        ("BMS CCL zero", battery(90, 54.0, 3.40, 3.38, ccl=0.0)),
        ("cell overvoltage", battery(90, 54.4, 3.56, 3.40)),
        ("high delta at high cell", battery(90, 54.4, 3.52, 3.33)),
    ]
    for label, snapshot in scenarios:
        controller_fresh = ChargerCurrentTaperController()
        decision = controller_fresh.decide(
            ChargerTelemetry(voltage_v=54.0, charge_stage="Absorb"),
            ChargerCurrentSettings(current_limit_a=80.0),
            snapshot,
        )
        print(f"  {label:24s} -> target {decision.target_current_a}A ({decision.reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
