"""Read-only Tasmota energy-monitor telemetry over the local HTTP API."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class TasmotaTelemetry:
    captured_at: datetime
    name: str
    host: str
    voltage_v: float
    current_a: float
    power_w: float
    apparent_power_va: float
    reactive_power_var: float
    power_factor: float
    energy_today_kwh: float
    energy_yesterday_kwh: float
    energy_total_kwh: float
    rolling_average_power_w: float | None = None


class TasmotaClient:
    def __init__(self, name: str, host: str, timeout: float = 3.0) -> None:
        self.name = name
        self.host = host
        self.timeout = timeout
        self._power_samples: deque[tuple[datetime, float]] = deque()
        self._rolling_window = timedelta(hours=3)

    def seed_power_samples(self, samples: list[tuple[datetime, float]]) -> None:
        for captured_at, power_w in sorted(samples):
            self._power_samples.append((captured_at.astimezone(timezone.utc), power_w))
        if self._power_samples:
            self._prune_power_samples(self._power_samples[-1][0])

    def read(self) -> TasmotaTelemetry:
        query = urlencode({"cmnd": "Status 10"})
        url = f"http://{self.host}/cm?{query}"
        # Supplying a same-device origin also keeps this reader compatible with
        # Tasmota devices whose optional HTTP referer check remains enabled.
        request = Request(url, headers={"Referer": f"http://{self.host}/"})
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured LAN device
            payload = json.load(response)
        try:
            energy = payload["StatusSNS"]["ENERGY"]
            captured_at = datetime.now(timezone.utc)
            power_w = float(energy["Power"])
            self._power_samples.append((captured_at, power_w))
            self._prune_power_samples(captured_at)
            rolling_average_power_w = sum(value for _, value in self._power_samples) / len(self._power_samples)
            return TasmotaTelemetry(
                captured_at=captured_at,
                name=self.name,
                host=self.host,
                voltage_v=float(energy["Voltage"]),
                current_a=float(energy["Current"]),
                power_w=power_w,
                apparent_power_va=float(energy["ApparentPower"]),
                reactive_power_var=float(energy["ReactivePower"]),
                power_factor=float(energy["Factor"]),
                energy_today_kwh=float(energy["Today"]),
                energy_yesterday_kwh=float(energy["Yesterday"]),
                energy_total_kwh=float(energy["Total"]),
                rolling_average_power_w=rolling_average_power_w,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid Tasmota Status 10 response from {self.host}") from exc

    def _prune_power_samples(self, now: datetime) -> None:
        cutoff = now - self._rolling_window
        while self._power_samples and self._power_samples[0][0] < cutoff:
            self._power_samples.popleft()
