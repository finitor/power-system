"""Read-only MidNite Classic Modbus telemetry adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from pymodbus.client import ModbusTcpClient


DEFAULT_HOST = "192.168.0.10"
DEFAULT_PORT = 502
DEFAULT_DEVICE_ID = 10


CHARGE_STAGES = {
    0: "Resting",
    3: "Absorb",
    4: "BulkMppt",
    5: "Float",
    6: "FloatMppt",
    7: "Equalize",
    10: "HyperVoc",
    18: "EqMppt",
}

CLASSIC_STATES = {
    0: "Resting",
    1: "Waking / Starting",
    2: "Waking / Starting",
    3: "MPPT or regulating voltage",
    4: "MPPT or regulating voltage",
    6: "MPPT or regulating voltage",
}

INFO_FLAGS = {
    0x00000001: "Classic over temperature",
    0x00000002: "EEPROM error",
    0x00000004: "Ethernet write lock",
    0x00000008: "Equalize in progress",
    0x00000100: "PV input lower than battery output",
    0x00000200: "Current limit reached",
    0x00000400: "HyperVoc",
    0x00002000: "Battery temperature sensor installed",
    0x00004000: "Aux1 on",
    0x00008000: "Aux2 on",
    0x00010000: "Ground fault",
    0x00020000: "Over current protect",
    0x00040000: "Arc fault",
    0x00080000: "Negative battery current",
    0x00200000: "Extra info available",
    0x00400000: "PV partial shade",
    0x00800000: "Watchdog reset",
    0x01000000: "Low battery voltage",
    0x02000000: "Stack jumper not installed",
    0x04000000: "Equalize done",
    0x08000000: "Temperature compensation shorted",
    0x10000000: "Unlock jumper not installed",
    0x20000000: "Extra jumper not installed",
    0x40000000: "PV input shorted",
}


@dataclass(frozen=True)
class RegisterBlock:
    start_register: int
    values: list[int]

    def get(self, register: int) -> int:
        index = register - self.start_register
        if index < 0 or index >= len(self.values):
            raise KeyError(register)
        return self.values[index]

    def as_dict(self) -> dict[int, int]:
        return {
            self.start_register + index: value
            for index, value in enumerate(self.values)
        }


@dataclass(frozen=True)
class ClassicTelemetry:
    captured_at: datetime
    battery_voltage_v: float
    pv_voltage_v: float
    battery_current_a: float
    daily_energy_kwh: float
    battery_power_w: int
    charge_stage_code: int
    charge_stage: str
    state_code: int
    state: str
    pv_current_a: float
    last_voc_v: float
    highest_input_voltage_v: float
    daily_amp_hours_ah: int
    lifetime_energy_kwh: int
    lifetime_amp_hours_ah: int
    info_flags: int
    active_flags: list[str]
    battery_temp_c: float
    fet_temp_c: float
    pcb_temp_c: float

    @property
    def is_hypervoc(self) -> bool:
        return self.charge_stage == "HyperVoc" or "HyperVoc" in self.active_flags


@dataclass(frozen=True)
class ClassicChargeSettings:
    captured_at: datetime
    battery_current_limit_a: float
    absorb_voltage_v: float
    float_voltage_v: float
    equalize_voltage_v: float
    sliding_current_limit_a: int
    absorb_time_s: int
    max_temp_comp_voltage_v: float
    min_temp_comp_voltage_v: float
    temp_comp_mv_per_c_cell: float
    mppt_mode_raw: int
    aux_function_word: int


def msb(value: int) -> int:
    return (value >> 8) & 0xFF


def lsb(value: int) -> int:
    return value & 0xFF


def u32(low_word: int, high_word: int) -> int:
    return (high_word << 16) + low_word


def flag_names(value: int) -> list[str]:
    return [name for bit, name in INFO_FLAGS.items() if value & bit]


def decode_live(block: RegisterBlock, captured_at: datetime | None = None) -> ClassicTelemetry:
    combo_stage = block.get(4120)
    charge_stage_code = msb(combo_stage)
    state_code = lsb(combo_stage)
    info_flags = u32(block.get(4130), block.get(4131))

    return ClassicTelemetry(
        captured_at=captured_at or datetime.now(timezone.utc),
        battery_voltage_v=block.get(4115) / 10,
        pv_voltage_v=block.get(4116) / 10,
        battery_current_a=block.get(4117) / 10,
        daily_energy_kwh=block.get(4118) / 10,
        battery_power_w=block.get(4119),
        charge_stage_code=charge_stage_code,
        charge_stage=CHARGE_STAGES.get(charge_stage_code, "unknown"),
        state_code=state_code,
        state=CLASSIC_STATES.get(state_code, "unknown"),
        pv_current_a=block.get(4121) / 10,
        last_voc_v=block.get(4122) / 10,
        highest_input_voltage_v=block.get(4123) / 10,
        daily_amp_hours_ah=block.get(4125),
        lifetime_energy_kwh=u32(block.get(4126), block.get(4127)),
        lifetime_amp_hours_ah=u32(block.get(4128), block.get(4129)),
        info_flags=info_flags,
        active_flags=flag_names(info_flags),
        battery_temp_c=block.get(4132) / 10,
        fet_temp_c=block.get(4133) / 10,
        pcb_temp_c=block.get(4134) / 10,
    )


def decode_settings(
    block: RegisterBlock,
    captured_at: datetime | None = None,
) -> ClassicChargeSettings:
    return ClassicChargeSettings(
        captured_at=captured_at or datetime.now(timezone.utc),
        battery_current_limit_a=block.get(4148) / 10,
        absorb_voltage_v=block.get(4149) / 10,
        float_voltage_v=block.get(4150) / 10,
        equalize_voltage_v=block.get(4151) / 10,
        sliding_current_limit_a=block.get(4152),
        absorb_time_s=block.get(4154),
        max_temp_comp_voltage_v=block.get(4155) / 10,
        min_temp_comp_voltage_v=block.get(4156) / 10,
        temp_comp_mv_per_c_cell=-(block.get(4157) / 10),
        mppt_mode_raw=block.get(4164),
        aux_function_word=block.get(4165),
    )


class ClassicClient:
    """Small read-only client for the Classic live-data registers."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        device_id: int = DEFAULT_DEVICE_ID,
        timeout: float = 3,
    ) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self.timeout = timeout

    def read(self) -> tuple[ClassicTelemetry, ClassicChargeSettings]:
        client = ModbusTcpClient(self.host, port=self.port, timeout=self.timeout)
        if not client.connect():
            raise ConnectionError(f"Could not connect to {self.host}:{self.port}")
        try:
            captured_at = datetime.now(timezone.utc)
            live = read_block(client, 4115, 20, self.device_id)
            settings = read_block(client, 4148, 18, self.device_id)
            return (
                decode_live(live, captured_at=captured_at),
                decode_settings(settings, captured_at=captured_at),
            )
        finally:
            client.close()

    def write_charge_settings(
        self,
        *,
        battery_current_limit_a: float | None = None,
        absorb_voltage_v: float | None = None,
        float_voltage_v: float | None = None,
        equalize_voltage_v: float | None = None,
        absorb_time_s: int | None = None,
        max_temp_comp_voltage_v: float | None = None,
    ) -> ClassicChargeSettings:
        writes: dict[int, int] = {}
        if battery_current_limit_a is not None:
            writes[4148] = round(battery_current_limit_a * 10)
        if absorb_voltage_v is not None:
            writes[4149] = round(absorb_voltage_v * 10)
        if float_voltage_v is not None:
            writes[4150] = round(float_voltage_v * 10)
        if equalize_voltage_v is not None:
            writes[4151] = round(equalize_voltage_v * 10)
        if absorb_time_s is not None:
            writes[4154] = absorb_time_s
        if max_temp_comp_voltage_v is not None:
            writes[4155] = round(max_temp_comp_voltage_v * 10)

        client = ModbusTcpClient(self.host, port=self.port, timeout=self.timeout)
        if not client.connect():
            raise ConnectionError(f"Could not connect to {self.host}:{self.port}")
        try:
            for register, value in writes.items():
                response = client.write_register(
                    address=register - 1,
                    value=value,
                    device_id=self.device_id,
                )
                if response.isError():
                    raise RuntimeError(f"Modbus write failed for register {register}: {response}")
            return decode_settings(read_block(client, 4148, 18, self.device_id), captured_at=datetime.now(timezone.utc))
        finally:
            client.close()


def read_block(
    client: ModbusTcpClient,
    start_register: int,
    count: int,
    device_id: int,
) -> RegisterBlock:
    # MidNite's map lists Modbus register numbers. pymodbus expects packet
    # addresses, which are register number minus one for the Classic.
    response = client.read_holding_registers(
        address=start_register - 1,
        count=count,
        device_id=device_id,
    )
    if response.isError():
        raise RuntimeError(
            f"Modbus read failed for registers {start_register}.."
            f"{start_register + count - 1}: {response}"
        )
    return RegisterBlock(start_register, list(response.registers))
