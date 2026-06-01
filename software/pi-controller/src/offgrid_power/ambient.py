"""Ambient temperature and humidity sensor adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time


@dataclass(frozen=True)
class AmbientTelemetry:
    temperature_c: float
    humidity_percent: float | None
    captured_at: datetime


class AmbientProbeDisconnected(RuntimeError):
    """Raised when a configured ambient probe is absent or electrically disconnected."""


class AmbientDhtClient:
    """Read a DHT11 or AM2302/DHT22 sensor through Adafruit's CircuitPython driver."""

    def __init__(self, gpio_pin: int = 4, sensor_type: str = "dht22", attempts: int = 3, retry_delay_s: float = 2.0) -> None:
        self.gpio_pin = gpio_pin
        self.sensor_type = sensor_type
        self.attempts = attempts
        self.retry_delay_s = retry_delay_s
        self._sensor = None

    def _load_sensor(self):
        if self._sensor is not None:
            return self._sensor

        try:
            import adafruit_dht
            import board
        except ImportError as exc:
            msg = "install sensor extras with: python -m pip install '.[sensors]'"
            raise RuntimeError(msg) from exc

        board_pin = getattr(board, f"D{self.gpio_pin}", None)
        if board_pin is None:
            msg = f"GPIO {self.gpio_pin} is not exposed as board.D{self.gpio_pin}"
            raise RuntimeError(msg)

        if self.sensor_type == "dht11":
            self._sensor = adafruit_dht.DHT11(board_pin, use_pulseio=False)
        else:
            self._sensor = adafruit_dht.DHT22(board_pin, use_pulseio=False)
        return self._sensor

    def read(self) -> AmbientTelemetry:
        sensor = self._load_sensor()
        last_error: Exception | None = None

        for attempt in range(1, self.attempts + 1):
            try:
                temperature_c = sensor.temperature
                humidity_percent = sensor.humidity

                if temperature_c is None or humidity_percent is None:
                    raise RuntimeError("DHT22 read returned no data")

                return AmbientTelemetry(
                    temperature_c=float(temperature_c),
                    humidity_percent=float(humidity_percent),
                    captured_at=datetime.now(timezone.utc),
                )
            except RuntimeError as exc:
                last_error = exc
                if attempt < self.attempts:
                    time.sleep(self.retry_delay_s)

        if last_error is not None:
            raise last_error
        raise RuntimeError("DHT read failed")


class AmbientDht22Client(AmbientDhtClient):
    """Read an AM2302/DHT22 sensor through Adafruit's CircuitPython driver."""

    def __init__(self, gpio_pin: int = 4, attempts: int = 3, retry_delay_s: float = 2.0) -> None:
        super().__init__(
            gpio_pin=gpio_pin,
            sensor_type="dht22",
            attempts=attempts,
            retry_delay_s=retry_delay_s,
        )


class AmbientDs18b20Client:
    """Read one DS18B20 probe through Linux's 1-Wire sysfs interface."""

    def __init__(self, device_id: str = "", devices_path: str = "/sys/bus/w1/devices") -> None:
        self.device_id = device_id
        self.devices_path = Path(devices_path)

    def _device_file(self) -> Path:
        if self.device_id:
            return self.devices_path / self.device_id / "w1_slave"

        matches = sorted(self.devices_path.glob("28-*/w1_slave"))
        if not matches:
            msg = "No DS18B20 devices found; enable 1-Wire and check wiring"
            raise AmbientProbeDisconnected(msg)
        return matches[0]

    def read(self) -> AmbientTelemetry:
        device_file = self._device_file()
        try:
            lines = device_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError as exc:
            msg = f"DS18B20 device not found: {device_file.parent.name}"
            raise AmbientProbeDisconnected(msg) from exc

        if len(lines) < 2 or not lines[0].strip().endswith("YES"):
            raise RuntimeError(f"DS18B20 CRC/read failed: {device_file.parent.name}")

        marker = "t="
        if marker not in lines[1]:
            raise RuntimeError(f"DS18B20 temperature missing: {device_file.parent.name}")

        raw_millic = int(lines[1].split(marker, maxsplit=1)[1])
        if raw_millic == 0 or raw_millic >= 80000 or raw_millic <= -55000:
            raise AmbientProbeDisconnected(
                f"DS18B20 returned implausible temperature; probe may be disconnected: {device_file.parent.name}"
            )

        return AmbientTelemetry(
            temperature_c=raw_millic / 1000,
            humidity_percent=None,
            captured_at=datetime.now(timezone.utc),
        )
