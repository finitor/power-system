"""Runtime configuration for the Pi supervisor."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ClassicConfig:
    host: str = "192.168.0.10"
    port: int = 502
    device_id: int = 10
    timeout_s: float = 3.0


@dataclass(frozen=True)
class DisplayConfig:
    refresh_seconds: float = 5.0
    clear_screen: bool = True


@dataclass(frozen=True)
class SupervisorConfig:
    classic: ClassicConfig
    display: DisplayConfig


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


def load_config() -> SupervisorConfig:
    return SupervisorConfig(
        classic=ClassicConfig(
            host=os.getenv("CLASSIC_HOST", "192.168.0.10"),
            port=env_int("CLASSIC_PORT", 502),
            device_id=env_int("CLASSIC_DEVICE_ID", 10),
            timeout_s=env_float("CLASSIC_TIMEOUT_SECONDS", 3.0),
        ),
        display=DisplayConfig(
            refresh_seconds=env_float("SUPERVISOR_REFRESH_SECONDS", 5.0),
            clear_screen=env_bool("SUPERVISOR_DISPLAY_CLEAR", True),
        ),
    )

