"""Runtime configuration for the Pi supervisor."""

from __future__ import annotations

from dataclasses import dataclass
import os

# Fallback when BATTERY_CAPACITY_AH is unset (2x Cubix 100). The env var in
# /etc/offgrid-power.env is the operator-set source of truth for installed
# bank capacity; update it when packs are added or removed.
DEFAULT_BATTERY_CAPACITY_AH = 200.0


@dataclass(frozen=True)
class ClassicConfig:
    host: str = "192.168.0.10"
    port: int = 502
    device_id: int = 10
    timeout_s: float = 3.0


@dataclass(frozen=True)
class EpeverConfig:
    device: str = ""
    baud: int = 115200
    unit: int = 1
    timeout_s: float = 1.5


@dataclass(frozen=True)
class DisplayConfig:
    refresh_seconds: float = 30.0
    clear_screen: bool = True
    battery_capacity_ah: float = DEFAULT_BATTERY_CAPACITY_AH
    unavailable_after_seconds: float = 300.0
    magnum_stale_after_seconds: float | None = None


@dataclass(frozen=True)
class BatteryCanConfig:
    protocol: str = "pylon"


@dataclass(frozen=True)
class AmbientConfig:
    enabled: bool = True
    kind: str = "ds18b20"
    gpio_pin: int = 4
    ds18b20_device_id: str = ""


@dataclass(frozen=True)
class RelayConfig:
    heat_fan_gpio: int = 17
    charge_disable_gpio: int = 27


@dataclass(frozen=True)
class NetworkConfig:
    lan_gateway: str = "192.168.0.1"
    lan_check_interval_s: float = 30.0


@dataclass(frozen=True)
class SupervisorConfig:
    classic: ClassicConfig
    epever: EpeverConfig
    display: DisplayConfig
    battery_can: BatteryCanConfig
    ambient: AmbientConfig
    network: NetworkConfig = NetworkConfig()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None or raw == "" else int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None or raw == "" else float(raw)


def env_optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    return None if raw is None or raw == "" else float(raw)


def load_config() -> SupervisorConfig:
    return SupervisorConfig(
        classic=ClassicConfig(
            host=os.getenv("CLASSIC_HOST", "192.168.0.10"),
            port=env_int("CLASSIC_PORT", 502),
            device_id=env_int("CLASSIC_DEVICE_ID", 10),
            timeout_s=env_float("CLASSIC_TIMEOUT_SECONDS", 3.0),
        ),
        epever=EpeverConfig(
            device=os.getenv("EPEVER_DEVICE", ""),
            baud=env_int("EPEVER_BAUD", 115200),
            unit=env_int("EPEVER_UNIT", 1),
            timeout_s=env_float("EPEVER_TIMEOUT_SECONDS", 1.5),
        ),
        display=DisplayConfig(
            refresh_seconds=env_float("SUPERVISOR_REFRESH_SECONDS", 30.0),
            clear_screen=env_bool("SUPERVISOR_DISPLAY_CLEAR", True),
            battery_capacity_ah=env_float("BATTERY_CAPACITY_AH", DEFAULT_BATTERY_CAPACITY_AH),
            unavailable_after_seconds=env_float("SUPERVISOR_UNAVAILABLE_AFTER_SECONDS", 300.0),
            magnum_stale_after_seconds=env_optional_float("MAGNUM_STALE_AFTER_SECONDS"),
        ),
        battery_can=BatteryCanConfig(
            protocol=os.getenv("BATTERY_CAN_PROTOCOL", "pylon"),
        ),
        ambient=AmbientConfig(
            enabled=env_bool("AMBIENT_SENSOR_ENABLED", True),
            kind=os.getenv("AMBIENT_SENSOR_KIND", "ds18b20"),
            gpio_pin=env_int("AMBIENT_DHT22_GPIO", 4),
            ds18b20_device_id=os.getenv("AMBIENT_DS18B20_DEVICE_ID", ""),
        ),
        network=NetworkConfig(
            lan_gateway=os.getenv("LAN_GATEWAY", "192.168.0.1"),
            lan_check_interval_s=env_float("LAN_CHECK_INTERVAL_SECONDS", 30.0),
        ),
    )


def load_relay_config() -> RelayConfig:
    return RelayConfig(
        heat_fan_gpio=env_int("RELAY_HEAT_FAN_GPIO", 17),
        charge_disable_gpio=env_int("RELAY_CHARGE_DISABLE_GPIO", 27),
    )
