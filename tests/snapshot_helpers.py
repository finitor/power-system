"""Shared test factories for supervisor snapshots.

make_snapshot() supplies every SupervisorSnapshot field with a None/empty
default so tests only state the fields they care about — adding a field to
SupervisorSnapshot then requires touching only this factory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import CanFrame, decode_pylon_snapshot
from offgrid_power.classic import ClassicTelemetry
from offgrid_power.supervisor import SupervisorSnapshot

DEFAULT_CAPTURED_AT = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)


def make_snapshot(**overrides) -> SupervisorSnapshot:
    fields: dict = {
        "captured_at": DEFAULT_CAPTURED_AT,
        "classic": None,
        "classic_settings": None,
        "battery": None,
        "battery_can_health": None,
        "ambient": None,
        "magnum": None,
        "errors": [],
    }
    fields.update(overrides)
    return SupervisorSnapshot(**fields)


def make_classic_telemetry(captured_at: datetime | None = None, **overrides) -> ClassicTelemetry:
    fields: dict = {
        "captured_at": captured_at or DEFAULT_CAPTURED_AT,
        "battery_voltage_v": 53.0,
        "pv_voltage_v": 28.0,
        "battery_current_a": 0.0,
        "daily_energy_kwh": 5.9,
        "battery_power_w": 0,
        "charge_stage_code": 0,
        "charge_stage": "Resting",
        "state_code": 0,
        "state": "Resting",
        "pv_current_a": 0.0,
        "last_voc_v": 101.0,
        "highest_input_voltage_v": 110.0,
        "daily_amp_hours_ah": 108,
        "lifetime_energy_kwh": 1234,
        "lifetime_amp_hours_ah": 5678,
        "info_flags": 0,
        "active_flags": [],
        "battery_temp_c": 17.0,
        "fet_temp_c": 31.0,
        "pcb_temp_c": 29.0,
    }
    fields.update(overrides)
    return ClassicTelemetry(**fields)


def make_battery_snapshot(soc_percent: int = 92):
    """Battery snapshot with the given SOC, fixed flow, and 200Ah capacity."""
    return decode_pylon_snapshot(
        [
            CanFrame(0x355, bytes([soc_percent, 0, 100, 0, 0, 0, 0, 0])),
            CanFrame(0x356, bytes.fromhex("B814D8FFA4000000")),
            CanFrame(0x379, bytes.fromhex("C800000000000000")),
        ]
    )
