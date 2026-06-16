from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from unittest import mock

from offgrid_power.charge_stage import ChargeStage
from offgrid_power.epever import EpeverClient, decode_settings, decode_telemetry


CAPTURED_AT = datetime(2026, 6, 11, 13, 55, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, registers):
        self.registers = registers

    def isError(self) -> bool:  # noqa: N802 - pymodbus interface name
        return False


class FakeModbusClient:
    """In-memory EPEver holding-register file for write tests."""

    # 0x9000-0x9013: type, capacity, temp-comp, then the 0x9007-0x9013 block.
    BASE = {
        0x9000: 6,     # Battery Type = User
        0x9001: 100,
        0x9002: 0xFFFD,  # temp comp, signed
        0x9007: 6400,  # OVD
        0x9008: 6000,  # charging limit
        0x9009: 6000,  # OV reconnect
        0x900A: 5830,  # equalize
        0x900B: 5760,  # boost
        0x900C: 5520,  # float
        0x900D: 5280,  # boost reconnect
        0x900E: 5040,
        0x900F: 4880,
        0x9010: 4800,
        0x9011: 4440,  # LVD
        0x9012: 4240,  # discharge limit
        0x9013: 8000,  # max charging current
    }

    def __init__(self, **_):
        self.regs = dict(self.BASE)
        self.writes: list[tuple[int, list[int]]] = []

    def connect(self) -> bool:
        return True

    def read_holding_registers(self, address, count, device_id):
        return _Resp([self.regs.get(address + i, 0) for i in range(count)])

    def write_registers(self, address, values, device_id):
        self.writes.append((address, list(values)))
        for i, value in enumerate(values):
            self.regs[address + i] = value
        return _Resp(list(values))

    def close(self) -> None:
        pass


class EpeverWriteTest(unittest.TestCase):
    def test_write_charge_voltages_is_read_modify_write(self) -> None:
        fake = FakeModbusClient()
        with mock.patch("offgrid_power.epever.ModbusSerialClient", return_value=fake):
            settings = EpeverClient().write_charge_voltages(
                equalize_v=58.4, boost_v=54.8, float_v=54.4
            )

        # One block write covering 0x9007-0x9012.
        block_writes = [w for w in fake.writes if w[0] == 0x9007]
        self.assertEqual(len(block_writes), 1)
        self.assertEqual(len(block_writes[0][1]), 12)
        # Targeted cells changed...
        self.assertEqual(fake.regs[0x900A], 5840)
        self.assertEqual(fake.regs[0x900B], 5480)
        self.assertEqual(fake.regs[0x900C], 5440)
        # ...protections preserved.
        self.assertEqual(fake.regs[0x9007], 6400)
        self.assertEqual(fake.regs[0x9011], 4440)
        self.assertEqual(fake.regs[0x9012], 4240)
        self.assertEqual(settings.boost_voltage_v, 54.8)

    def test_write_charge_voltages_requires_user_battery_type(self) -> None:
        fake = FakeModbusClient()
        fake.regs[0x9000] = 1  # Sealed, not User
        with mock.patch("offgrid_power.epever.ModbusSerialClient", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "Battery Type must be User"):
                EpeverClient().write_charge_voltages(boost_v=54.8)
        self.assertEqual(fake.writes, [])

    def test_write_charge_voltages_accepts_user_code_zero(self) -> None:
        # The EPEver reports User as code 0 (6 also maps to User); both pass.
        fake = FakeModbusClient()
        fake.regs[0x9000] = 0
        with mock.patch("offgrid_power.epever.ModbusSerialClient", return_value=fake):
            EpeverClient().write_charge_voltages(boost_v=54.8)
        self.assertEqual(fake.regs[0x900B], 5480)

    def test_write_charge_voltages_partial_only_touches_named_cell(self) -> None:
        fake = FakeModbusClient()
        with mock.patch("offgrid_power.epever.ModbusSerialClient", return_value=fake):
            EpeverClient().write_charge_voltages(float_v=54.0)
        self.assertEqual(fake.regs[0x900C], 5400)
        self.assertEqual(fake.regs[0x900B], 5760)  # boost untouched
        self.assertEqual(fake.regs[0x900A], 5830)  # equalize untouched


class EpeverDecodeTest(unittest.TestCase):
    def test_decodes_live_probe_registers(self) -> None:
        telemetry = decode_telemetry(
            rated=[42, 4, 25000, 10000, 61248, 7, 4800, 10000, 61248],
            live=[0, 0, 0, 0, 5311, 0, 0, 0],
            temperatures=[0, 0],
            soc=[2055],
            # status is 0x3200..0x3202; the charging-equipment-status word is
            # 0x3202 (status[2]) on the TEP, not 0x3201 (which reads zero).
            status=[0, 0, 0],
            captured_at=CAPTURED_AT,
        )

        self.assertEqual(telemetry.captured_at, CAPTURED_AT)
        self.assertEqual(telemetry.pv_voltage_v, 0.0)
        self.assertEqual(telemetry.pv_current_a, 0.0)
        self.assertEqual(telemetry.pv_power_w, 0.0)
        self.assertEqual(telemetry.battery_voltage_v, 53.11)
        self.assertEqual(telemetry.battery_current_a, 0.0)
        self.assertEqual(telemetry.battery_power_w, 0)
        self.assertIsNone(telemetry.battery_soc_percent)
        self.assertEqual(telemetry.charging_status, "No charging")
        self.assertEqual(telemetry.rated_pv_voltage_v, 250.0)
        self.assertEqual(telemetry.rated_charging_current_a, 100.0)
        self.assertEqual(telemetry.rated_battery_voltage_v, 48.0)

    def test_reads_charging_status_from_0x3202_not_0x3201(self) -> None:
        # Live capture 2026-06-16 while charging at ~3.4 A: the generic-map
        # 0x3201 stayed zero, while 0x3202=0x0009 carried running + Boost.
        # 0x3201 zero must NOT mask the real status at 0x3202.
        telemetry = decode_telemetry(
            rated=[42, 4, 25000, 10000, 61248, 7, 4800, 10000, 61248],
            live=[16320, 113, 18600, 0, 5436, 343, 0, 0],
            temperatures=[0, 0],
            soc=[2055],
            status=[0, 0, 0x0009],
            captured_at=CAPTURED_AT,
        )

        self.assertEqual(telemetry.status_raw, 0x0009)
        self.assertEqual(telemetry.charging_status, "Boost")
        self.assertEqual(telemetry.canonical_stage, ChargeStage.ABSORB)

    def test_decodes_battery_settings(self) -> None:
        settings = decode_settings(
            [6, 200, 300, 1, 60, 2, 4, 5470, 5360, 5360, 5330, 5330, 5000, 4970, 4800],
            captured_at=CAPTURED_AT,
        )

        self.assertEqual(settings.battery_type, "User")
        self.assertEqual(settings.battery_capacity_ah, 200)
        self.assertEqual(settings.boost_voltage_v, 54.7)
        self.assertEqual(settings.float_voltage_v, 53.6)
        self.assertEqual(settings.low_voltage_disconnect_v, 49.7)
        self.assertIsNone(settings.max_charging_current_a)

    def test_decodes_max_charging_current_when_present(self) -> None:
        settings = decode_settings(
            [
                0,
                198,
                300,
                0,
                0,
                0,
                0,
                6400,
                6000,
                6000,
                5830,
                5760,
                5520,
                5280,
                5040,
                4880,
                4800,
                4440,
                4240,
                10000,
            ],
            captured_at=CAPTURED_AT,
        )

        self.assertEqual(settings.battery_type, "User")
        self.assertEqual(settings.equalize_voltage_v, 58.3)
        self.assertEqual(settings.max_charging_current_a, 100.0)


if __name__ == "__main__":
    unittest.main()
