"""LAN and WAN reachability monitor.

Runs a background thread that probes the local gateway and an external host
on a slow interval. Results are available lock-free as bool | None properties
(None until the first probe completes).

LAN unreachable is a fault — it means the WiFi router or Ethernet switch is
down, which cuts Modbus TCP access to the Classic charge controller.
WAN unreachable is expected during normal operation when Starlink is not
active; suppress errors for WAN-dependent subsystems (metric export) when
wan_reachable is False rather than logging noise.
"""

from __future__ import annotations

from datetime import datetime, timezone
import socket
import subprocess
from threading import Event, Lock, Thread
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .metrics import TelemetryEvent


class NetworkMonitor:
    def __init__(
        self,
        gateway: str = "192.168.0.1",
        check_interval_s: float = 30.0,
        wan_host: str = "8.8.8.8",
        wan_port: int = 53,
        wan_timeout_s: float = 3.0,
    ) -> None:
        self.gateway = gateway
        self.check_interval_s = check_interval_s
        self._wan_host = wan_host
        self._wan_port = wan_port
        self._wan_timeout_s = wan_timeout_s
        self._lock = Lock()
        self._lan_reachable: bool | None = None
        self._wan_reachable: bool | None = None
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def lan_reachable(self) -> bool | None:
        with self._lock:
            return self._lan_reachable

    @property
    def wan_reachable(self) -> bool | None:
        with self._lock:
            return self._wan_reachable

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="network-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            lan = self._check_lan()
            wan = self._check_wan()
            with self._lock:
                self._lan_reachable = lan
                self._wan_reachable = wan
            self._stop.wait(timeout=self.check_interval_s)

    def _check_lan(self) -> bool:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "1", self.gateway],
                capture_output=True,
                timeout=3,
            )
            return result.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    def _check_wan(self) -> bool:
        try:
            with socket.create_connection((self._wan_host, self._wan_port), timeout=self._wan_timeout_s):
                return True
        except OSError:
            return False


class WanReachabilityTracker:
    """Emits a TelemetryEvent on each WAN up/down transition.

    WAN is expected to be down for long periods (Starlink not active), so
    per-tick sampling would produce mostly redundant rows. Tracking only
    transitions keeps the event store informative without noise.
    """

    def __init__(self) -> None:
        self._was_reachable: bool | None = None

    def observe(self, wan_reachable: bool | None) -> TelemetryEvent | None:
        if wan_reachable is None:
            return None
        previous = self._was_reachable
        self._was_reachable = wan_reachable
        if previous is None or previous == wan_reachable:
            return None
        from .metrics import TelemetryEvent  # avoid circular import (metrics -> load -> supervisor -> network_monitor)
        return TelemetryEvent(
            captured_at=datetime.now(timezone.utc),
            source="network",
            event="wan_up" if wan_reachable else "wan_down",
            detail={},
        )
