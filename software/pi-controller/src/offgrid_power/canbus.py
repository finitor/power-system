"""CAN bus adapter discovery helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import subprocess
import time
from pathlib import Path
from typing import Iterable

ARPHRD_CAN = "280"
STM32_DFU_VENDOR_ID = "0483"
STM32_DFU_PRODUCT_ID = "df11"


class BatteryCanProtocol(str, Enum):
    PYLON = "pylon"
    ECOWORTHY_VICTRON = "ecoworthy-victron"

    @classmethod
    def normalize(cls, value: "BatteryCanProtocol | str") -> "BatteryCanProtocol":
        if isinstance(value, cls):
            return value
        cleaned = value.strip().lower().replace("_", "-")
        aliases = {
            "eco-worthy-victron": cls.ECOWORTHY_VICTRON,
            "ecoworthy-victron": cls.ECOWORTHY_VICTRON,
            "eco-victron": cls.ECOWORTHY_VICTRON,
            "victron": cls.ECOWORTHY_VICTRON,
            "pylon": cls.PYLON,
            "pylontech": cls.PYLON,
        }
        try:
            return aliases[cleaned]
        except KeyError as exc:
            choices = ", ".join(protocol.value for protocol in cls)
            raise ValueError(f"unknown battery CAN protocol {value!r}; choose one of: {choices}") from exc


@dataclass(frozen=True)
class UsbDevice:
    path: Path
    vendor_id: str
    product_id: str
    product: str
    serial: str


@dataclass(frozen=True)
class CanBusHealth:
    interface: str
    socketcan_present: bool
    dfu_devices: tuple[UsbDevice, ...]

    @property
    def ok(self) -> bool:
        return self.socketcan_present and not self.dfu_devices

    def status_message(self) -> str:
        if self.dfu_devices:
            devices = ", ".join(
                f"{device.product or 'STM32 DFU'}"
                f"{f' serial={device.serial}' if device.serial else ''}"
                for device in self.dfu_devices
            )
            return f"CAN adapter is in DFU/bootloader mode: {devices}"
        if not self.socketcan_present:
            return f"CAN interface {self.interface} is not present"
        return f"CAN interface {self.interface} is present"


@dataclass(frozen=True)
class CanFrame:
    arbitration_id: int
    data: bytes
    timestamp: float | None = None
    is_extended_id: bool = False


@dataclass(frozen=True)
class PylonChargeLimits:
    charge_voltage_limit_v: float
    charge_current_limit_a: float
    discharge_current_limit_a: float
    discharge_voltage_limit_v: float


@dataclass(frozen=True)
class PylonStateOfCharge:
    soc_percent: int
    soh_percent: int


@dataclass(frozen=True)
class PylonMeasurements:
    voltage_v: float
    current_a: float
    temperature_c: float


@dataclass(frozen=True)
class PylonStatus:
    module_count: int
    protection_flags: tuple[str, ...]
    alarm_flags: tuple[str, ...]
    manufacturer_marker: str


@dataclass(frozen=True)
class PylonRequestFlags:
    charge_enable: bool
    discharge_enable: bool
    force_charge_1: bool
    force_charge_2: bool
    full_charge_request: bool


@dataclass(frozen=True)
class PylonExtendedMeasurements:
    min_cell_voltage_v: float | None = None
    max_cell_voltage_v: float | None = None
    min_cell_temperature_c: float | None = None
    max_cell_temperature_c: float | None = None
    installed_capacity_ah: float | None = None


@dataclass(frozen=True)
class PylonCanSnapshot:
    charge_limits: PylonChargeLimits | None = None
    state_of_charge: PylonStateOfCharge | None = None
    measurements: PylonMeasurements | None = None
    status: PylonStatus | None = None
    request_flags: PylonRequestFlags | None = None
    manufacturer: str | None = None
    extended_measurements: PylonExtendedMeasurements | None = None
    raw_frames: dict[int, bytes] | None = None

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.charge_limits is not None:
            limits = self.charge_limits
            lines.append(
                "0x351 limits: "
                f"charge {limits.charge_voltage_limit_v:.1f} V, "
                f"charge current {limits.charge_current_limit_a:.1f} A, "
                f"discharge current {limits.discharge_current_limit_a:.1f} A, "
                f"discharge floor {limits.discharge_voltage_limit_v:.1f} V"
            )
        if self.state_of_charge is not None:
            state = self.state_of_charge
            lines.append(f"0x355 state: SOC {state.soc_percent}% / SOH {state.soh_percent}%")
        if self.measurements is not None:
            measurements = self.measurements
            lines.append(
                "0x356 measurements: "
                f"{measurements.voltage_v:.2f} V, "
                f"{measurements.current_a:.1f} A, "
                f"{measurements.temperature_c:.1f} C"
            )
        if self.status is not None:
            status = self.status
            protection = ", ".join(status.protection_flags) if status.protection_flags else "none"
            alarms = ", ".join(status.alarm_flags) if status.alarm_flags else "none"
            lines.append(
                f"0x359 status: modules {status.module_count}, "
                f"marker {status.manufacturer_marker!r}, protections {protection}, alarms {alarms}"
            )
        if self.request_flags is not None:
            flags = self.request_flags
            enabled = []
            if flags.charge_enable:
                enabled.append("charge enable")
            if flags.discharge_enable:
                enabled.append("discharge enable")
            if flags.force_charge_1:
                enabled.append("force charge 1")
            if flags.force_charge_2:
                enabled.append("force charge 2")
            if flags.full_charge_request:
                enabled.append("full charge request")
            lines.append(f"0x35C requests: {', '.join(enabled) if enabled else 'none'}")
        if self.manufacturer is not None:
            lines.append(f"0x35E manufacturer: {self.manufacturer!r}")
        if self.extended_measurements is not None:
            extended = self.extended_measurements
            parts = []
            if extended.min_cell_voltage_v is not None and extended.max_cell_voltage_v is not None:
                parts.append(
                    f"cell voltage {extended.min_cell_voltage_v:.3f}-{extended.max_cell_voltage_v:.3f} V"
                )
            if extended.min_cell_temperature_c is not None and extended.max_cell_temperature_c is not None:
                parts.append(
                    f"cell temp {extended.min_cell_temperature_c:.1f}-{extended.max_cell_temperature_c:.1f} C"
                )
            if extended.installed_capacity_ah is not None:
                parts.append(f"installed capacity {extended.installed_capacity_ah:.0f} Ah")
            if parts:
                lines.append(f"Extended candidates: {', '.join(parts)}")

        raw_frames = self.raw_frames or {}
        known_ids = {0x351, 0x355, 0x356, 0x359, 0x35C, 0x35E, 0x373, 0x379}
        unknown_ids = sorted(frame_id for frame_id in raw_frames if frame_id not in known_ids)
        if unknown_ids:
            rendered_ids = ", ".join(f"0x{frame_id:03X}" for frame_id in unknown_ids)
            lines.append(f"Undecoded frames present: {rendered_ids}")
        return lines


class BatteryCanClient:
    def __init__(
        self,
        interface: str = "can0",
        receive_seconds: float = 1.5,
        protocol: BatteryCanProtocol | str = BatteryCanProtocol.PYLON,
    ) -> None:
        self.interface = interface
        self.receive_seconds = receive_seconds
        self.protocol = BatteryCanProtocol.normalize(protocol)

    def read(self) -> PylonCanSnapshot:
        try:
            import can
        except ImportError as exc:
            raise RuntimeError("python-can is not installed") from exc

        frames: list[CanFrame] = []
        deadline = time.monotonic() + self.receive_seconds
        with can.Bus(interface="socketcan", channel=self.interface) as bus:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                message = bus.recv(timeout=remaining)
                if message is None:
                    break
                frames.append(
                    CanFrame(
                        arbitration_id=message.arbitration_id,
                        data=bytes(message.data),
                        timestamp=message.timestamp,
                        is_extended_id=message.is_extended_id,
                    )
                )

        if not frames:
            raise RuntimeError(f"no CAN frames received on {self.interface}")
        return decode_battery_snapshot(frames, self.protocol)


def socketcan_interfaces(sys_class_net: Path = Path("/sys/class/net")) -> list[str]:
    interfaces: list[str] = []
    for interface_path in sorted(sys_class_net.iterdir()):
        type_path = interface_path / "type"
        if type_path.exists() and type_path.read_text(encoding="utf-8").strip() == ARPHRD_CAN:
            interfaces.append(interface_path.name)
    return interfaces


def canbus_health(
    interface: str = "can0",
    sys_class_net: Path = Path("/sys/class/net"),
    sys_bus_usb: Path = Path("/sys/bus/usb/devices"),
) -> CanBusHealth:
    return CanBusHealth(
        interface=interface,
        socketcan_present=interface in socketcan_interfaces(sys_class_net),
        dfu_devices=tuple(stm32_dfu_devices(sys_bus_usb)),
    )


def interface_state(interface: str, sys_class_net: Path = Path("/sys/class/net")) -> str:
    return _read_optional(sys_class_net / interface / "operstate")


def ensure_socketcan_interface_up(
    interface: str = "can0",
    bitrate: int = 500000,
    *,
    listen_only: bool = True,
    sys_class_net: Path = Path("/sys/class/net"),
    runner=subprocess.run,
) -> bool:
    """Configure and raise a SocketCAN interface when it is present but down."""
    if interface not in socketcan_interfaces(sys_class_net):
        return False
    if interface_state(interface, sys_class_net) != "down":
        return False

    type_command = ["ip", "link", "set", interface, "type", "can", "bitrate", str(bitrate)]
    if listen_only:
        type_command.extend(["listen-only", "on"])

    runner(type_command, check=True)
    runner(["ip", "link", "set", interface, "up"], check=True)
    return True


def configure_socketcan_interface(
    interface: str = "can0",
    bitrate: int = 500000,
    *,
    listen_only: bool = True,
    sys_class_net: Path = Path("/sys/class/net"),
    runner=subprocess.run,
) -> bool:
    """Force a SocketCAN interface to a known bitrate and mode."""
    if interface not in socketcan_interfaces(sys_class_net):
        return False

    runner(["ip", "link", "set", interface, "down"], check=False)

    type_command = ["ip", "link", "set", interface, "type", "can", "bitrate", str(bitrate)]
    if listen_only:
        type_command.extend(["listen-only", "on"])
    else:
        type_command.extend(["listen-only", "off"])

    runner(type_command, check=True)
    runner(["ip", "link", "set", interface, "up"], check=True)
    return True


def stm32_dfu_devices(sys_bus_usb: Path = Path("/sys/bus/usb/devices")) -> list[UsbDevice]:
    devices: list[UsbDevice] = []
    for device_path in sorted(sys_bus_usb.iterdir()):
        vendor_id = _read_optional(device_path / "idVendor")
        product_id = _read_optional(device_path / "idProduct")
        if vendor_id != STM32_DFU_VENDOR_ID or product_id != STM32_DFU_PRODUCT_ID:
            continue

        devices.append(
            UsbDevice(
                path=device_path,
                vendor_id=vendor_id,
                product_id=product_id,
                product=_read_optional(device_path / "product"),
                serial=_read_optional(device_path / "serial"),
            )
        )
    return devices


def _read_optional(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def decode_pylon_snapshot(frames: Iterable[CanFrame]) -> PylonCanSnapshot:
    raw_frames = {frame.arbitration_id: frame.data for frame in frames}
    return PylonCanSnapshot(
        charge_limits=_decode_charge_limits(raw_frames.get(0x351)),
        state_of_charge=_decode_state_of_charge(raw_frames.get(0x355)),
        measurements=_decode_measurements(raw_frames.get(0x356)),
        status=_decode_status(raw_frames.get(0x359)),
        request_flags=_decode_request_flags(raw_frames.get(0x35C)),
        manufacturer=_decode_ascii(raw_frames.get(0x35E)),
        extended_measurements=_decode_extended_measurements(raw_frames),
        raw_frames=raw_frames,
    )


def decode_battery_snapshot(
    frames: Iterable[CanFrame],
    protocol: BatteryCanProtocol | str = BatteryCanProtocol.PYLON,
) -> PylonCanSnapshot:
    normalized = BatteryCanProtocol.normalize(protocol)
    if normalized in {BatteryCanProtocol.PYLON, BatteryCanProtocol.ECOWORTHY_VICTRON}:
        return decode_pylon_snapshot(frames)

    raise ValueError(f"unsupported battery CAN protocol {normalized.value!r}")


def candump_log_frames(lines: Iterable[str]) -> list[CanFrame]:
    frames: list[CanFrame] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        timestamp = None
        if stripped.startswith("("):
            timestamp_text, stripped = stripped.split(")", maxsplit=1)
            timestamp = float(timestamp_text[1:])
            stripped = stripped.strip()

        parts = stripped.split()
        if len(parts) == 2 and "#" in parts[1]:
            frame_text = parts[1]
        elif len(parts) >= 3 and "#" in parts[2]:
            frame_text = parts[2]
        else:
            continue

        frame_id_text, data_text = frame_text.split("#", maxsplit=1)
        arbitration_id = int(frame_id_text, 16)
        frames.append(
            CanFrame(
                arbitration_id,
                bytes.fromhex(data_text),
                timestamp,
                is_extended_id=arbitration_id > 0x7FF,
            )
        )
    return frames


def _decode_charge_limits(data: bytes | None) -> PylonChargeLimits | None:
    if data is None or len(data) < 8:
        return None
    return PylonChargeLimits(
        charge_voltage_limit_v=_u16(data, 0) * 0.1,
        charge_current_limit_a=_s16(data, 2) * 0.1,
        discharge_current_limit_a=_s16(data, 4) * 0.1,
        discharge_voltage_limit_v=_u16(data, 6) * 0.1,
    )


def _decode_state_of_charge(data: bytes | None) -> PylonStateOfCharge | None:
    if data is None or len(data) < 4:
        return None
    return PylonStateOfCharge(soc_percent=_u16(data, 0), soh_percent=_u16(data, 2))


def _decode_measurements(data: bytes | None) -> PylonMeasurements | None:
    if data is None or len(data) < 6:
        return None
    return PylonMeasurements(
        voltage_v=_s16(data, 0) * 0.01,
        current_a=_s16(data, 2) * 0.1,
        temperature_c=_s16(data, 4) * 0.1,
    )


def _decode_status(data: bytes | None) -> PylonStatus | None:
    if data is None or len(data) < 7:
        return None
    return PylonStatus(
        module_count=data[4],
        protection_flags=_status_flags(data[0], data[1], _PROTECTION_FLAGS),
        alarm_flags=_status_flags(data[2], data[3], _ALARM_FLAGS),
        manufacturer_marker=bytes(data[5:7]).decode("ascii", errors="replace"),
    )


def _decode_request_flags(data: bytes | None) -> PylonRequestFlags | None:
    if data is None or len(data) < 1:
        return None
    value = data[0]
    return PylonRequestFlags(
        charge_enable=bool(value & 0x80),
        discharge_enable=bool(value & 0x40),
        force_charge_1=bool(value & 0x20),
        force_charge_2=bool(value & 0x10),
        full_charge_request=bool(value & 0x08),
    )


def _decode_ascii(data: bytes | None) -> str | None:
    if data is None:
        return None
    return data.decode("ascii", errors="replace").rstrip(" \x00")


def _decode_extended_measurements(raw_frames: dict[int, bytes]) -> PylonExtendedMeasurements | None:
    cell_voltage_v = None
    cell_temperature_c = None
    data_373 = raw_frames.get(0x373)
    if data_373 is not None and len(data_373) >= 8:
        cell_voltage_v = (_u16(data_373, 0) * 0.001, _u16(data_373, 2) * 0.001)
        cell_temperature_c = (_u16(data_373, 4) - 273.15, _u16(data_373, 6) - 273.15)

    installed_capacity_ah = None
    data_379 = raw_frames.get(0x379)
    if data_379 is not None and len(data_379) >= 2:
        installed_capacity_ah = _u16(data_379, 0)

    if cell_voltage_v is None and cell_temperature_c is None and installed_capacity_ah is None:
        return None

    return PylonExtendedMeasurements(
        min_cell_voltage_v=cell_voltage_v[0] if cell_voltage_v is not None else None,
        max_cell_voltage_v=cell_voltage_v[1] if cell_voltage_v is not None else None,
        min_cell_temperature_c=cell_temperature_c[0] if cell_temperature_c is not None else None,
        max_cell_temperature_c=cell_temperature_c[1] if cell_temperature_c is not None else None,
        installed_capacity_ah=installed_capacity_ah,
    )


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], byteorder="little", signed=False)


def _s16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], byteorder="little", signed=True)


def _status_flags(
    byte_0: int,
    byte_1: int,
    mapping: dict[tuple[int, int], str],
) -> tuple[str, ...]:
    flags = []
    for (byte_index, mask), label in mapping.items():
        value = byte_0 if byte_index == 0 else byte_1
        if value & mask:
            flags.append(label)
    return tuple(flags)


_PROTECTION_FLAGS = {
    (0, 0x80): "discharge over current",
    (0, 0x40): "cell under temperature",
    (0, 0x20): "cell over temperature",
    (0, 0x10): "cell/module under voltage",
    (0, 0x08): "cell/module over voltage",
    (1, 0x80): "system error",
    (1, 0x40): "charge over current",
}

_ALARM_FLAGS = {
    (0, 0x80): "discharge high current",
    (0, 0x40): "cell low temperature",
    (0, 0x20): "cell high temperature",
    (0, 0x10): "cell/module low voltage",
    (0, 0x08): "cell/module high voltage",
    (1, 0x80): "internal communication fail",
    (1, 0x40): "charge high current",
}
