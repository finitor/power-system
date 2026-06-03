from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.classic import (
    ETHERNET_UNLOCK_REGISTER,
    CLASSIC_SERIAL_REGISTER,
    RegisterBlock,
    decode_live,
    decode_settings,
    unlock_ethernet_writes,
)


class FakeModbusResponse:
    def __init__(self, registers: list[int] | None = None, error: bool = False) -> None:
        self.registers = registers or []
        self.error = error

    def isError(self) -> bool:
        return self.error


class FakeModbusClient:
    def __init__(self) -> None:
        self.writes: list[tuple[int, int, int]] = []

    def read_holding_registers(
        self,
        *,
        address: int,
        count: int,
        device_id: int,
    ) -> FakeModbusResponse:
        self.assert_serial_read(address, count)
        return FakeModbusResponse([0x1234, 0x5678])

    def write_register(
        self,
        *,
        address: int,
        value: int,
        device_id: int,
    ) -> FakeModbusResponse:
        self.writes.append((address, value, device_id))
        return FakeModbusResponse()

    def assert_serial_read(self, address: int, count: int) -> None:
        if address != CLASSIC_SERIAL_REGISTER - 1 or count != 2:
            raise AssertionError((address, count))


class ClassicDecodeTest(unittest.TestCase):
    def test_decode_live_registers_from_observed_classic_sample(self) -> None:
        block = RegisterBlock(
            start_register=4115,
            values=[
                502, 1018, 16, 32, 80, 1027, 10, 1236, 1968, 0,
                60, 29317, 0, 52852, 0, 12292, 45568, 196, 463, 464,
            ],
        )

        telemetry = decode_live(
            block,
            captured_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        )

        self.assertEqual(telemetry.battery_voltage_v, 50.2)
        self.assertEqual(telemetry.pv_voltage_v, 101.8)
        self.assertEqual(telemetry.battery_current_a, 1.6)
        self.assertEqual(telemetry.battery_power_w, 80)
        self.assertEqual(telemetry.charge_stage_code, 4)
        self.assertEqual(telemetry.charge_stage, "BulkMppt")
        self.assertEqual(telemetry.state_code, 3)
        self.assertEqual(telemetry.state, "MPPT or regulating voltage")
        self.assertFalse(telemetry.is_hypervoc)
        self.assertEqual(telemetry.battery_temp_c, 19.6)

    def test_decode_live_registers_exposes_hypervoc_state(self) -> None:
        block = RegisterBlock(
            start_register=4115,
            values=[
                548, 2050, 0, 0, 0, 0x0A00, 0, 2010, 2180, 0,
                0, 0, 0, 0, 0, 0x0400, 0, 164, 453, 435,
            ],
        )

        telemetry = decode_live(
            block,
            captured_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        )

        self.assertEqual(telemetry.charge_stage, "HyperVoc")
        self.assertIn("HyperVoc", telemetry.active_flags)
        self.assertTrue(telemetry.is_hypervoc)
        self.assertEqual(telemetry.last_voc_v, 201.0)
        self.assertEqual(telemetry.highest_input_voltage_v, 218.0)

    def test_decode_settings_registers_from_observed_classic_sample(self) -> None:
        block = RegisterBlock(
            start_register=4148,
            values=[
                400, 592, 540, 648, 400, 0, 7200, 648, 528,
                50, 0, 0, 0, 0, 3600, 30, 11, 20993,
            ],
        )

        settings = decode_settings(
            block,
            captured_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        )

        self.assertEqual(settings.battery_current_limit_a, 40.0)
        self.assertEqual(settings.absorb_voltage_v, 59.2)
        self.assertEqual(settings.float_voltage_v, 54.0)
        self.assertEqual(settings.equalize_voltage_v, 64.8)
        self.assertEqual(settings.absorb_time_s, 7200)
        self.assertEqual(settings.temp_comp_mv_per_c_cell, -5.0)

    def test_unlock_ethernet_writes_uses_classic_serial_words(self) -> None:
        client = FakeModbusClient()

        unlock_ethernet_writes(client, device_id=10)

        self.assertEqual(
            client.writes,
            [
                (ETHERNET_UNLOCK_REGISTER - 1, 0x1234, 10),
                (ETHERNET_UNLOCK_REGISTER, 0x5678, 10),
            ],
        )


if __name__ == "__main__":
    unittest.main()
