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
from offgrid_power.epever import EpeverChargeSettings, EpeverTelemetry
from offgrid_power.magnum import MagnumSnapshot
from offgrid_power.supervisor import SupervisorSnapshot

DEFAULT_CAPTURED_AT = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)


def make_magnum_snapshot(**overrides) -> MagnumSnapshot:
    fields: dict = {
        "captured_at": DEFAULT_CAPTURED_AT,
        "dc_volts": 53.2,
        "dc_amps": 4,
        "ac_volts_out": 120,
        "ac_volts_in": 0,
        "ac_amps_in": 0,
        "ac_amps_out": 1,
        "ac_freq_hz": 60.0,
        "inverter_on": True,
        "charger_on": False,
        "status_name": "INVERT",
        "fault_name": "NONE",
        "battery_temp_c": 25,
        "transformer_temp_c": 37,
        "fet_temp_c": 30,
        "absorb_v": 54.4,
        "float_v": 54.4,
        "absorb_time_hr": 3.0,
        "shore_amps": 30,
        "charger_amps_pct": 0,
    }
    fields.update(overrides)
    return MagnumSnapshot(**fields)


def make_snapshot(**overrides) -> SupervisorSnapshot:
    fields: dict = {
        "captured_at": DEFAULT_CAPTURED_AT,
        "classic": None,
        "classic_settings": None,
        "epever": None,
        "epever_settings": None,
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


def make_epever_telemetry(captured_at: datetime | None = None, **overrides) -> EpeverTelemetry:
    fields: dict = {
        "captured_at": captured_at or DEFAULT_CAPTURED_AT,
        "pv_voltage_v": 0.0,
        "pv_current_a": 0.0,
        "pv_power_w": 0.0,
        "battery_voltage_v": 53.11,
        "battery_current_a": 0.0,
        "battery_power_w": 0,
        "battery_soc_percent": None,
        "battery_temp_c": 0.0,
        "pcb_temp_c": 0.0,
        "status_raw": 0,
        "charging_status": "No charging",
        "rated_battery_voltage_v": 4.2,
        "rated_charging_current_a": 0.04,
        "rated_pv_voltage_v": 250.0,
    }
    fields.update(overrides)
    return EpeverTelemetry(**fields)


def make_epever_settings(captured_at: datetime | None = None, **overrides) -> EpeverChargeSettings:
    fields: dict = {
        "captured_at": captured_at or DEFAULT_CAPTURED_AT,
        "battery_type_code": 6,
        "battery_type": "User",
        "battery_capacity_ah": 200,
        "temperature_compensation_mv_per_c_cell": 300,
        "over_voltage_disconnect_v": 0.01,
        "charging_limit_voltage_v": 0.6,
        "over_voltage_reconnect_v": 0.02,
        "equalize_voltage_v": 0.04,
        "boost_voltage_v": 54.7,
        "float_voltage_v": 53.6,
        "boost_reconnect_voltage_v": 53.6,
        "low_voltage_reconnect_v": 53.3,
        "under_voltage_recover_v": 53.3,
        "under_voltage_warning_v": 50.0,
        "low_voltage_disconnect_v": 49.7,
        "discharging_limit_voltage_v": 48.0,
        "max_charging_current_a": 80.0,
        "boost_time_minutes": 120,
        "equalize_time_minutes": 10,
    }
    fields.update(overrides)
    return EpeverChargeSettings(**fields)


def make_battery_snapshot(
    soc_percent: int = 92,
    min_cell_temperature_c: float | None = None,
    max_cell_temperature_c: float | None = None,
    charge_enable: bool | None = True,
):
    """Battery snapshot with the given SOC, fixed flow, and 200Ah capacity.

    Pass min/max cell temperatures in °C to include extended measurements. By
    default a healthy 0x35C request-flags frame is included (charge + discharge
    enabled); pass ``charge_enable=None`` to omit it entirely (models a dropped
    request-flags frame), or ``charge_enable=False`` for a genuine BMS stop.
    """
    frames = [
        CanFrame(0x355, bytes([soc_percent, 0, 100, 0, 0, 0, 0, 0])),
        CanFrame(0x356, bytes.fromhex("B814D8FFA4000000")),
        CanFrame(0x379, bytes.fromhex("C800000000000000")),
    ]
    if charge_enable is not None:
        # 0x35C byte 0: charge_enable=0x80, discharge_enable=0x40.
        flags_byte = 0x40 | (0x80 if charge_enable else 0x00)
        frames.append(CanFrame(0x35C, bytes([flags_byte, 0, 0, 0, 0, 0, 0, 0])))
    if min_cell_temperature_c is not None or max_cell_temperature_c is not None:
        min_k = round((min_cell_temperature_c or 0.0) + 273.15)
        max_k = round((max_cell_temperature_c or min_cell_temperature_c or 0.0) + 273.15)
        frames.append(CanFrame(0x373, b"\x00\x00\x00\x00"
                               + min_k.to_bytes(2, "little")
                               + max_k.to_bytes(2, "little")))
    return decode_pylon_snapshot(frames)
