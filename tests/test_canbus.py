from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.canbus import (
    BatteryCanProtocol,
    CanFrame,
    canbus_health,
    candump_log_frames,
    configure_socketcan_interface,
    decode_battery_snapshot,
    decode_pylon_snapshot,
    ensure_socketcan_interface_up,
    interface_state,
    socketcan_interfaces,
    stm32_dfu_devices,
)


class CanBusDiscoveryTest(unittest.TestCase):
    def test_socketcan_interfaces_detects_can_link_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sys_class_net = Path(temp_dir)
            (sys_class_net / "can0").mkdir()
            (sys_class_net / "can0" / "type").write_text("280\n", encoding="utf-8")
            (sys_class_net / "eth0").mkdir()
            (sys_class_net / "eth0" / "type").write_text("1\n", encoding="utf-8")

            self.assertEqual(socketcan_interfaces(sys_class_net), ["can0"])

    def test_interface_state_reads_operstate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sys_class_net = Path(temp_dir)
            (sys_class_net / "can0").mkdir()
            (sys_class_net / "can0" / "operstate").write_text("down\n", encoding="utf-8")

            self.assertEqual(interface_state("can0", sys_class_net), "down")

    def test_ensure_socketcan_interface_up_configures_down_interface(self) -> None:
        commands = []

        def fake_runner(command, check):
            commands.append((command, check))

        with tempfile.TemporaryDirectory() as temp_dir:
            sys_class_net = Path(temp_dir)
            (sys_class_net / "can0").mkdir()
            (sys_class_net / "can0" / "type").write_text("280\n", encoding="utf-8")
            (sys_class_net / "can0" / "operstate").write_text("down\n", encoding="utf-8")

            changed = ensure_socketcan_interface_up(
                "can0",
                bitrate=500000,
                sys_class_net=sys_class_net,
                runner=fake_runner,
            )

        self.assertTrue(changed)
        self.assertEqual(
            commands,
            [
                (["ip", "link", "set", "can0", "type", "can", "bitrate", "500000", "listen-only", "on"], True),
                (["ip", "link", "set", "can0", "up"], True),
            ],
        )

    def test_ensure_socketcan_interface_up_leaves_running_interface_alone(self) -> None:
        commands = []

        with tempfile.TemporaryDirectory() as temp_dir:
            sys_class_net = Path(temp_dir)
            (sys_class_net / "can0").mkdir()
            (sys_class_net / "can0" / "type").write_text("280\n", encoding="utf-8")
            (sys_class_net / "can0" / "operstate").write_text("unknown\n", encoding="utf-8")

            changed = ensure_socketcan_interface_up(
                "can0",
                sys_class_net=sys_class_net,
                runner=lambda command, check: commands.append(command),
            )

        self.assertFalse(changed)
        self.assertEqual(commands, [])

    def test_configure_socketcan_interface_forces_bitrate_and_listen_only(self) -> None:
        commands = []

        def fake_runner(command, check):
            commands.append((command, check))

        with tempfile.TemporaryDirectory() as temp_dir:
            sys_class_net = Path(temp_dir)
            (sys_class_net / "can0").mkdir()
            (sys_class_net / "can0" / "type").write_text("280\n", encoding="utf-8")

            changed = configure_socketcan_interface(
                "can0",
                bitrate=250000,
                sys_class_net=sys_class_net,
                runner=fake_runner,
            )

        self.assertTrue(changed)
        self.assertEqual(
            commands,
            [
                (["ip", "link", "set", "can0", "down"], False),
                (["ip", "link", "set", "can0", "type", "can", "bitrate", "250000", "listen-only", "on"], True),
                (["ip", "link", "set", "can0", "up"], True),
            ],
        )

    def test_stm32_dfu_devices_detects_bootloader_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sys_bus_usb = Path(temp_dir)
            device = sys_bus_usb / "1-1.3"
            device.mkdir()
            (device / "idVendor").write_text("0483\n", encoding="utf-8")
            (device / "idProduct").write_text("df11\n", encoding="utf-8")
            (device / "product").write_text("DFU in FS Mode\n", encoding="utf-8")
            (device / "serial").write_text("208634B94B45\n", encoding="utf-8")

            devices = stm32_dfu_devices(sys_bus_usb)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].product, "DFU in FS Mode")
        self.assertEqual(devices[0].serial, "208634B94B45")

    def test_canbus_health_reports_dfu_mode(self) -> None:
        with tempfile.TemporaryDirectory() as net_dir, tempfile.TemporaryDirectory() as usb_dir:
            sys_bus_usb = Path(usb_dir)
            device = sys_bus_usb / "1-1.3"
            device.mkdir()
            (device / "idVendor").write_text("0483\n", encoding="utf-8")
            (device / "idProduct").write_text("df11\n", encoding="utf-8")
            (device / "product").write_text("DFU in FS Mode\n", encoding="utf-8")
            (device / "serial").write_text("208634B94B45\n", encoding="utf-8")

            health = canbus_health(
                interface="can0",
                sys_class_net=Path(net_dir),
                sys_bus_usb=sys_bus_usb,
            )

        self.assertFalse(health.ok)
        self.assertFalse(health.socketcan_present)
        self.assertIn("DFU/bootloader", health.status_message())
        self.assertIn("208634B94B45", health.status_message())


class PylonCanDecodeTest(unittest.TestCase):
    def test_battery_protocol_aliases_normalize(self) -> None:
        self.assertEqual(BatteryCanProtocol.normalize("pylontech"), BatteryCanProtocol.PYLON)
        self.assertEqual(BatteryCanProtocol.normalize("victron"), BatteryCanProtocol.ECOWORTHY_VICTRON)
        self.assertEqual(BatteryCanProtocol.normalize("eco-worthy_victron"), BatteryCanProtocol.ECOWORTHY_VICTRON)

    def test_ecoworthy_victron_profile_decodes_known_shared_frames(self) -> None:
        snapshot = decode_battery_snapshot(
            [
                CanFrame(0x351, bytes.fromhex("4802D007D007C001")),
                CanFrame(0x355, bytes.fromhex("5600640098210000")),
                CanFrame(0x356, bytes.fromhex("46155A00A0000000")),
                CanFrame(0x35E, bytes.fromhex("45434F2D4C465034")),
                CanFrame(0x373, bytes.fromhex("490D4E0D20012101")),
                CanFrame(0x379, bytes.fromhex("C800000000000000")),
            ],
            BatteryCanProtocol.ECOWORTHY_VICTRON,
        )

        self.assertIsNotNone(snapshot.state_of_charge)
        self.assertEqual(snapshot.state_of_charge.soc_percent, 86)
        self.assertIsNotNone(snapshot.measurements)
        self.assertAlmostEqual(snapshot.measurements.voltage_v, 54.46)
        self.assertAlmostEqual(snapshot.measurements.current_a, 9.0)
        self.assertEqual(snapshot.manufacturer, "ECO-LFP4")
        self.assertIsNotNone(snapshot.extended_measurements)
        self.assertAlmostEqual(snapshot.extended_measurements.min_cell_voltage_v, 3.401)
        self.assertEqual(snapshot.extended_measurements.installed_capacity_ah, 200)

    def test_decodes_known_pylon_frames(self) -> None:
        snapshot = decode_pylon_snapshot(
            [
                CanFrame(0x351, bytes.fromhex("4802D007D007C001")),
                CanFrame(0x355, bytes.fromhex("1E00640000000000")),
                CanFrame(0x356, bytes.fromhex("7914000071000000")),
                CanFrame(0x359, bytes.fromhex("0000000002504E00")),
                CanFrame(0x35C, bytes.fromhex("C000000000000000")),
                CanFrame(0x35E, bytes.fromhex("50594C4F4E202020")),
                CanFrame(0x370, bytes.fromhex("76006C00CF0CCA0C")),
                CanFrame(0x373, bytes.fromhex("CA0CCF0C1B011C01")),
                CanFrame(0x374, bytes.fromhex("3032313400000000")),
                CanFrame(0x375, bytes.fromhex("3032313000000000")),
                CanFrame(0x379, bytes.fromhex("C800000000000000")),
            ]
        )

        self.assertIsNotNone(snapshot.charge_limits)
        self.assertAlmostEqual(snapshot.charge_limits.charge_voltage_limit_v, 58.4)
        self.assertAlmostEqual(snapshot.charge_limits.charge_current_limit_a, 200.0)
        self.assertAlmostEqual(snapshot.charge_limits.discharge_current_limit_a, 200.0)
        self.assertAlmostEqual(snapshot.charge_limits.discharge_voltage_limit_v, 44.8)

        self.assertIsNotNone(snapshot.state_of_charge)
        self.assertEqual(snapshot.state_of_charge.soc_percent, 30)
        self.assertEqual(snapshot.state_of_charge.soh_percent, 100)

        self.assertIsNotNone(snapshot.measurements)
        self.assertAlmostEqual(snapshot.measurements.voltage_v, 52.41)
        self.assertAlmostEqual(snapshot.measurements.current_a, 0.0)
        self.assertAlmostEqual(snapshot.measurements.temperature_c, 11.3)

        self.assertIsNotNone(snapshot.status)
        self.assertEqual(snapshot.status.module_count, 2)
        self.assertEqual(snapshot.status.manufacturer_marker, "PN")
        self.assertEqual(snapshot.status.protection_flags, ())
        self.assertEqual(snapshot.status.alarm_flags, ())

        self.assertIsNotNone(snapshot.request_flags)
        self.assertTrue(snapshot.request_flags.charge_enable)
        self.assertTrue(snapshot.request_flags.discharge_enable)
        self.assertFalse(snapshot.request_flags.full_charge_request)

        self.assertEqual(snapshot.manufacturer, "PYLON")

        self.assertIsNotNone(snapshot.extended_measurements)
        self.assertAlmostEqual(snapshot.extended_measurements.min_cell_voltage_v, 3.274)
        self.assertAlmostEqual(snapshot.extended_measurements.max_cell_voltage_v, 3.279)
        self.assertEqual(snapshot.extended_measurements.min_cell_pack_number, 2)
        self.assertEqual(snapshot.extended_measurements.min_cell_number, 14)
        self.assertEqual(snapshot.extended_measurements.min_cell_location_text(), "02:14")
        self.assertEqual(snapshot.extended_measurements.max_cell_pack_number, 2)
        self.assertEqual(snapshot.extended_measurements.max_cell_number, 10)
        self.assertEqual(snapshot.extended_measurements.max_cell_location_text(), "02:10")
        self.assertAlmostEqual(snapshot.extended_measurements.min_cell_temperature_c, 10.0)
        self.assertAlmostEqual(snapshot.extended_measurements.max_cell_temperature_c, 11.0)
        self.assertEqual(snapshot.extended_measurements.installed_capacity_ah, 200)
        self.assertIn("min 02:14, max 02:10", "\n".join(snapshot.summary_lines()))
        self.assertIn("0x370", snapshot.summary_lines()[-1])

    def test_parses_candump_log_frames(self) -> None:
        frames = candump_log_frames(
            [
                "(1780064750.959438) can0 351#4802D007D007C001",
                "(1780064750.980815) can0 355#1E00640000000000",
            ]
        )

        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].timestamp, 1780064750.959438)
        self.assertEqual(frames[0].arbitration_id, 0x351)
        self.assertEqual(frames[0].data, bytes.fromhex("4802D007D007C001"))

    def test_parses_extended_candump_log_frame_hint(self) -> None:
        frames = candump_log_frames(["(1780064750.959438) can0 09F8017F#0102030405060708"])

        self.assertEqual(frames[0].arbitration_id, 0x09F8017F)
        self.assertTrue(frames[0].is_extended_id)


if __name__ == "__main__":
    unittest.main()
