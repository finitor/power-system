"""EPEver TEP-series Modbus RTU telemetry client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from pymodbus.client import ModbusSerialClient


DEFAULT_DEVICE = "/dev/epever-rs485"
DEFAULT_BAUD = 115200
DEFAULT_UNIT = 1

BATTERY_TYPES = {
    1: "Sealed",
    2: "Gel",
    3: "Flooded",
    6: "User",
}

CHARGING_STATUS = {
    0: "No charging",
    1: "Float",
    2: "Boost",
    3: "Equalize",
}


@dataclass(frozen=True)
class EpeverTelemetry:
    captured_at: datetime
    pv_voltage_v: float
    pv_current_a: float
    pv_power_w: float
    battery_voltage_v: float
    battery_current_a: float
    battery_power_w: int
    battery_soc_percent: int | None
    battery_temp_c: float | None
    device_temp_c: float | None
    status_raw: int
    charging_status: str
    rated_battery_voltage_v: float
    rated_charging_current_a: float
    rated_pv_voltage_v: float


@dataclass(frozen=True)
class EpeverChargeSettings:
    captured_at: datetime
    battery_type_code: int
    battery_type: str
    battery_capacity_ah: int
    temperature_compensation_mv_per_c_cell: int
    over_voltage_disconnect_v: float
    charging_limit_voltage_v: float
    over_voltage_reconnect_v: float
    equalize_voltage_v: float
    boost_voltage_v: float
    float_voltage_v: float
    boost_reconnect_voltage_v: float
    low_voltage_reconnect_v: float
    under_voltage_recover_v: float
    under_voltage_warning_v: float
    low_voltage_disconnect_v: float
    discharging_limit_voltage_v: float


class EpeverClient:
    """Read-only Modbus RTU client for EPEver TEP charge controllers."""

    def __init__(
        self,
        device: str = DEFAULT_DEVICE,
        baud: int = DEFAULT_BAUD,
        unit: int = DEFAULT_UNIT,
        timeout: float = 1.5,
    ) -> None:
        self.device = device
        self.baud = baud
        self.unit = unit
        self.timeout = timeout

    def read(self) -> tuple[EpeverTelemetry, EpeverChargeSettings]:
        client = ModbusSerialClient(
            port=self.device,
            baudrate=self.baud,
            parity="N",
            stopbits=1,
            bytesize=8,
            timeout=self.timeout,
        )
        if not client.connect():
            raise ConnectionError(f"Could not open {self.device}")
        try:
            captured_at = datetime.now(timezone.utc)
            rated = read_input_registers(client, 0x3000, 9, self.unit)
            live = read_input_registers(client, 0x3100, 8, self.unit)
            temperatures = read_input_registers(client, 0x3110, 2, self.unit)
            soc = read_input_registers(client, 0x311A, 1, self.unit)
            status = read_input_registers(client, 0x3200, 2, self.unit)
            settings = read_holding_registers(client, 0x9000, 15, self.unit)
            return (
                decode_telemetry(rated, live, temperatures, soc, status, captured_at),
                decode_settings(settings, captured_at),
            )
        finally:
            client.close()


def decode_telemetry(
    rated: list[int],
    live: list[int],
    temperatures: list[int],
    soc: list[int],
    status: list[int],
    captured_at: datetime | None = None,
) -> EpeverTelemetry:
    battery_current_a = live[5] / 100
    battery_voltage_v = live[4] / 100
    status_raw = status[1]
    charging_code = (status_raw >> 2) & 0x03
    return EpeverTelemetry(
        captured_at=captured_at or datetime.now(timezone.utc),
        pv_voltage_v=live[0] / 100,
        pv_current_a=live[1] / 100,
        pv_power_w=_u32(live[2], live[3]) / 100,
        battery_voltage_v=battery_voltage_v,
        battery_current_a=battery_current_a,
        battery_power_w=round(battery_voltage_v * battery_current_a),
        battery_soc_percent=soc[0] if 0 <= soc[0] <= 100 else None,
        battery_temp_c=_signed_16(temperatures[0]) / 100,
        device_temp_c=_signed_16(temperatures[1]) / 100,
        status_raw=status_raw,
        charging_status=CHARGING_STATUS.get(charging_code, "unknown"),
        rated_battery_voltage_v=rated[6] / 100,
        rated_charging_current_a=rated[3] / 100,
        rated_pv_voltage_v=rated[2] / 100,
    )


def decode_settings(settings: list[int], captured_at: datetime | None = None) -> EpeverChargeSettings:
    battery_type_code = settings[0]
    return EpeverChargeSettings(
        captured_at=captured_at or datetime.now(timezone.utc),
        battery_type_code=battery_type_code,
        battery_type=BATTERY_TYPES.get(battery_type_code, "unknown"),
        battery_capacity_ah=settings[1],
        temperature_compensation_mv_per_c_cell=_signed_16(settings[2]),
        over_voltage_disconnect_v=settings[3] / 100,
        charging_limit_voltage_v=settings[4] / 100,
        over_voltage_reconnect_v=settings[5] / 100,
        equalize_voltage_v=settings[6] / 100,
        boost_voltage_v=settings[7] / 100,
        float_voltage_v=settings[8] / 100,
        boost_reconnect_voltage_v=settings[9] / 100,
        low_voltage_reconnect_v=settings[10] / 100,
        under_voltage_recover_v=settings[11] / 100,
        under_voltage_warning_v=settings[12] / 100,
        low_voltage_disconnect_v=settings[13] / 100,
        discharging_limit_voltage_v=settings[14] / 100,
    )


def read_input_registers(client: ModbusSerialClient, address: int, count: int, unit: int) -> list[int]:
    response = client.read_input_registers(address=address, count=count, device_id=unit)
    if response.isError():
        raise RuntimeError(f"EPEver input read failed for 0x{address:04X}..0x{address + count - 1:04X}: {response}")
    return list(response.registers)


def read_holding_registers(client: ModbusSerialClient, address: int, count: int, unit: int) -> list[int]:
    response = client.read_holding_registers(address=address, count=count, device_id=unit)
    if response.isError():
        raise RuntimeError(f"EPEver holding read failed for 0x{address:04X}..0x{address + count - 1:04X}: {response}")
    return list(response.registers)


def _u32(low_word: int, high_word: int) -> int:
    return (high_word << 16) + low_word


def _signed_16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value
