"""EPEver TEP-series Modbus RTU telemetry client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from pymodbus.client import ModbusSerialClient

from offgrid_power.charge_stage import (
    ChargeStage,
    NormalizedStage,
    epever_stage,
    normalize_epever_stage,
)


DEFAULT_DEVICE = "/dev/epever-rs485"
DEFAULT_BAUD = 115200
DEFAULT_UNIT = 1

BATTERY_TYPES = {
    0: "User",
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

# Control coils (function 0x01 read / 0x05 write) -- a separate address space
# from the 0x9000 holding registers (coil 0 != holding register 0). Only these
# two are ever written. NB: coil 0x000D = "restore system defaults" is
# DESTRUCTIVE (wipes the User profile / setpoints) and is never written here.
COIL_CHARGE_ON_OFF = 0x0000           # 1 = charging enabled, 0 = true hard stop
COIL_CLEAR_GENERATION_STATS = 0x000E  # pulse True to zero accumulated energy


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
    generated_today_kwh: float | None = None
    generated_total_kwh: float | None = None

    @property
    def canonical_stage(self) -> ChargeStage:
        return normalize_epever_stage(self.charging_status)

    @property
    def stage(self) -> NormalizedStage:
        return epever_stage(self.charging_status)


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
    max_charging_current_a: float | None = None


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
            # The TEP10425's status block is offset by one from the generic
            # Tracer map: the "charging equipment status" word the generic doc
            # places at 0x3201 actually lives at 0x3202 here (0x3201 reads a
            # flat zero even while charging). Read through 0x3202 so the decode
            # has the real status word. Confirmed live 2026-06-16: charging at
            # ~3.4 A read 0x3202=0x0009 (running + Boost) while 0x3201=0x0000.
            status = read_input_registers(client, 0x3200, 3, self.unit)
            # Energy-statistics block; we want generated-energy-today
            # (0x330C/0x330D) and the monotonic lifetime total (0x3310/0x3311).
            # The TEP stores these big-endian by word (opposite the live
            # registers), so decode high-word-first; see decode_telemetry.
            energy = read_input_registers(client, 0x3300, 18, self.unit)
            settings = read_holding_registers(client, 0x9000, 20, self.unit)
            return (
                decode_telemetry(rated, live, temperatures, soc, status, energy, captured_at),
                decode_settings(settings, captured_at),
            )
        finally:
            client.close()

    def write_max_charging_current(self, current_a: float) -> EpeverChargeSettings:
        """Write BAT Max Charging Current and return the settings readback.

        Solar Guardian uses Modbus function 0x10 even for one-register writes
        on this controller. The register is centiamps: 100.00 A -> 10000.
        """
        if current_a < 1.0 or current_a > 100.0:
            raise ValueError(f"EPEver charging current out of range: {current_a}")
        raw_current = round(current_a * 100)
        if raw_current < 0 or raw_current > 0xFFFF:
            raise ValueError(f"EPEver charging current out of range: {current_a}")

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
            response = client.write_registers(address=0x9013, values=[raw_current], device_id=self.unit)
            if response.isError():
                raise RuntimeError(f"EPEver current-limit write failed: {response}")
            settings = decode_settings(
                read_holding_registers(client, 0x9000, 20, self.unit),
                datetime.now(timezone.utc),
            )
            readback = settings.max_charging_current_a
            if readback is None or abs(readback - current_a) >= 0.1:
                raise RuntimeError(
                    "EPEver current-limit readback mismatch: "
                    f"wrote {current_a:.1f} A, read {readback!r} A"
                )
            return settings
        finally:
            client.close()

    def _open_client(self) -> ModbusSerialClient:
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
        return client

    def read_charge_enabled(self) -> bool:
        """Read the charge on/off coil (0x0000): True = charging enabled."""
        client = self._open_client()
        try:
            response = client.read_coils(
                address=COIL_CHARGE_ON_OFF, count=1, device_id=self.unit
            )
            if response.isError():
                raise RuntimeError(f"EPEver charge-coil read failed: {response}")
            return bool(response.bits[0])
        finally:
            client.close()

    def set_charging(self, enabled: bool) -> bool:
        """Write the charge on/off coil (0x0000) and return the read-back state.

        This is the true 0 A hard stop the current taper cannot give (0x9013
        floors at 1 A). Reads the coil back and verifies it took.
        """
        client = self._open_client()
        try:
            write = client.write_coil(
                address=COIL_CHARGE_ON_OFF, value=enabled, device_id=self.unit
            )
            if write.isError():
                raise RuntimeError(f"EPEver charge-coil write failed: {write}")
            response = client.read_coils(
                address=COIL_CHARGE_ON_OFF, count=1, device_id=self.unit
            )
            if response.isError():
                raise RuntimeError(f"EPEver charge-coil read-back failed: {response}")
            state = bool(response.bits[0])
            if state != enabled:
                raise RuntimeError(
                    f"EPEver charge-coil read-back mismatch: wrote {enabled}, read {state}"
                )
            return state
        finally:
            client.close()

    def clear_generation_statistics(self) -> None:
        """Pulse the clear-generation-statistics coil (0x000E).

        NOTE: observed to be a NO-OP on the TEP10425 (2026-06-16) -- the write
        succeeds but the energy block is byte-identical before and after, so the
        panel's "Clear Accumulated Energy" maps to some other coil here. Kept for
        other EPEver models and because the value-anchored windowed-consumption
        design needs no clear. On models where it works it is DESTRUCTIVE and
        irreversible. Distinct from coil 0x000D (restore factory defaults),
        which we never write.
        """
        client = self._open_client()
        try:
            write = client.write_coil(
                address=COIL_CLEAR_GENERATION_STATS, value=True, device_id=self.unit
            )
            if write.isError():
                raise RuntimeError(f"EPEver clear-generation-statistics write failed: {write}")
        finally:
            client.close()

    def write_charge_voltages(
        self,
        *,
        equalize_v: float | None = None,
        boost_v: float | None = None,
        float_v: float | None = None,
        boost_reconnect_v: float | None = None,
    ) -> EpeverChargeSettings:
        """Read-modify-write the EPEver charge-voltage block.

        The voltage block 0x9007-0x9012 (centivolts) is written as a unit via
        function 0x10, so we read the live block, overwrite only the named
        cells, and write the whole block back. The protection thresholds
        (OVD/reconnect/LVD/discharge) are preserved untouched. Precondition:
        Battery Type must already be User (the EPEver register reports this as
        code 0, though 6 also maps to User); the controller rejects
        charge-voltage writes otherwise.
        """
        targets = {0x900A: equalize_v, 0x900B: boost_v, 0x900C: float_v, 0x900D: boost_reconnect_v}
        if all(v is None for v in targets.values()):
            raise ValueError("write_charge_voltages: nothing to write")
        for label, value in (
            ("equalize", equalize_v),
            ("boost", boost_v),
            ("float", float_v),
            ("boost reconnect", boost_reconnect_v),
        ):
            if value is not None and not (0.0 < value <= 65.0):
                raise ValueError(f"EPEver {label} voltage out of range: {value}")

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
            battery_type_code = read_holding_registers(client, 0x9000, 1, self.unit)[0]
            if BATTERY_TYPES.get(battery_type_code) != "User":
                raise RuntimeError(
                    "EPEver Battery Type must be User before charge-voltage "
                    f"writes; controller reports code {battery_type_code} "
                    f"({BATTERY_TYPES.get(battery_type_code, 'unknown')})"
                )
            block = read_holding_registers(client, 0x9007, 12, self.unit)
            for address, value in targets.items():
                if value is not None:
                    block[address - 0x9007] = round(value * 100)
            charging_limit = block[0x9008 - 0x9007]
            equalize = block[0x900A - 0x9007]
            boost = block[0x900B - 0x9007]
            float_ = block[0x900C - 0x9007]
            boost_reconnect = block[0x900D - 0x9007]
            if boost > equalize:
                raise ValueError(
                    "EPEver boost voltage cannot exceed equalize voltage: "
                    f"boost {boost / 100:.2f} V, equalize {equalize / 100:.2f} V"
                )
            if boost_reconnect >= float_:
                raise ValueError(
                    "EPEver boost reconnect voltage must be below float voltage: "
                    f"boost reconnect {boost_reconnect / 100:.2f} V, float {float_ / 100:.2f} V"
                )
            for label, raw in (("equalize", equalize), ("boost", boost), ("float", float_)):
                if raw > charging_limit:
                    raise ValueError(
                        "EPEver charge voltage cannot exceed charging-limit voltage: "
                        f"{label} {raw / 100:.2f} V, limit {charging_limit / 100:.2f} V"
                    )
            response = client.write_registers(address=0x9007, values=block, device_id=self.unit)
            if response.isError():
                raise RuntimeError(f"EPEver voltage-block write failed: {response}")
            settings = decode_settings(
                read_holding_registers(client, 0x9000, 20, self.unit),
                datetime.now(timezone.utc),
            )
            for label, value, readback in (
                ("equalize", equalize_v, settings.equalize_voltage_v),
                ("boost", boost_v, settings.boost_voltage_v),
                ("float", float_v, settings.float_voltage_v),
                ("boost reconnect", boost_reconnect_v, settings.boost_reconnect_voltage_v),
            ):
                if value is not None and abs(readback - value) >= 0.01:
                    raise RuntimeError(
                        f"EPEver {label} readback mismatch: "
                        f"wrote {value:.2f} V, read {readback:.2f} V"
                    )
            return settings
        finally:
            client.close()


def decode_telemetry(
    rated: list[int],
    live: list[int],
    temperatures: list[int],
    soc: list[int],
    status: list[int],
    energy: list[int],
    captured_at: datetime | None = None,
) -> EpeverTelemetry:
    battery_current_a = live[5] / 100
    battery_voltage_v = live[4] / 100
    # 0x3202 (status[2]) is the TEP's charging-equipment-status word; see read().
    status_raw = status[2]
    charging_code = (status_raw >> 2) & 0x03
    # Generated energy today: 0x330C/0x330D (indices 12/13 from 0x3300), decoded
    # high-word-first (TEP word order is opposite the live registers). Provisional
    # scaling (/100 = kWh); validate against the local-midnight rollover now that
    # the device RTC is set. The reset *event* is observable regardless of scale.
    generated_today_kwh = None
    if len(energy) >= 14:
        generated_today_kwh = ((energy[12] << 16) + energy[13]) / 100
    # Lifetime generated-energy total, 0x3310/0x3311 (indices 16/17). Identified
    # empirically: it survived the RTC date jump that reset today/month and keeps
    # climbing with charging, so it's the monotonic accumulator the windowed
    # consumption calc differences. Provisional scaling (/100 = kWh); confirm by
    # monotonic growth in the logged series and a panel cross-check.
    generated_total_kwh = None
    if len(energy) >= 18:
        generated_total_kwh = ((energy[16] << 16) + energy[17]) / 100
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
        generated_today_kwh=generated_today_kwh,
        generated_total_kwh=generated_total_kwh,
    )


def decode_settings(settings: list[int], captured_at: datetime | None = None) -> EpeverChargeSettings:
    battery_type_code = settings[0]
    max_charging_current_a = settings[19] / 100 if len(settings) > 19 else None
    voltage_offset = 7 if len(settings) > 18 else 3
    return EpeverChargeSettings(
        captured_at=captured_at or datetime.now(timezone.utc),
        battery_type_code=battery_type_code,
        battery_type=BATTERY_TYPES.get(battery_type_code, "unknown"),
        battery_capacity_ah=settings[1],
        temperature_compensation_mv_per_c_cell=_signed_16(settings[2]),
        over_voltage_disconnect_v=settings[voltage_offset] / 100,
        charging_limit_voltage_v=settings[voltage_offset + 1] / 100,
        over_voltage_reconnect_v=settings[voltage_offset + 2] / 100,
        equalize_voltage_v=settings[voltage_offset + 3] / 100,
        boost_voltage_v=settings[voltage_offset + 4] / 100,
        float_voltage_v=settings[voltage_offset + 5] / 100,
        boost_reconnect_voltage_v=settings[voltage_offset + 6] / 100,
        low_voltage_reconnect_v=settings[voltage_offset + 7] / 100,
        under_voltage_recover_v=settings[voltage_offset + 8] / 100,
        under_voltage_warning_v=settings[voltage_offset + 9] / 100,
        low_voltage_disconnect_v=settings[voltage_offset + 10] / 100,
        discharging_limit_voltage_v=settings[voltage_offset + 11] / 100,
        max_charging_current_a=max_charging_current_a,
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
