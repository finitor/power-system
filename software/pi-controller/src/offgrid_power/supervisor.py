"""Read-only supervisory snapshot assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .ambient import AmbientDhtClient, AmbientDs18b20Client, AmbientProbeDisconnected, AmbientTelemetry
from .canbus import BatteryCanClient, CanBusHealth, PylonCanSnapshot, canbus_health
from .classic import ClassicChargeSettings, ClassicClient, ClassicTelemetry


@dataclass(frozen=True)
class SupervisorSnapshot:
    captured_at: datetime
    classic: ClassicTelemetry | None
    classic_settings: ClassicChargeSettings | None
    battery: PylonCanSnapshot | None
    battery_can_health: CanBusHealth | None
    ambient: AmbientTelemetry | None
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


AmbientClient = AmbientDhtClient | AmbientDs18b20Client


class Supervisor:
    def __init__(
        self,
        classic: ClassicClient | None,
        ambient: AmbientClient | None = None,
        battery: BatteryCanClient | None = None,
        battery_can_interface: str | None = None,
    ) -> None:
        self.classic = classic
        self.ambient = ambient
        self.battery = battery
        self.battery_can_interface = battery_can_interface

    def read_snapshot(self) -> SupervisorSnapshot:
        errors: list[str] = []
        classic: ClassicTelemetry | None = None
        classic_settings: ClassicChargeSettings | None = None
        battery: PylonCanSnapshot | None = None
        battery_can_health: CanBusHealth | None = None
        ambient: AmbientTelemetry | None = None

        if self.classic is not None:
            try:
                classic, classic_settings = self.classic.read()
            except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
                errors.append(f"Classic read failed: {exc}")

        if self.ambient is not None:
            try:
                ambient = self.ambient.read()
            except AmbientProbeDisconnected:
                ambient = None
            except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
                errors.append(f"Ambient sensor read failed: {exc}")

        if self.battery_can_interface is not None:
            battery_can_health = canbus_health(self.battery_can_interface)
            if not battery_can_health.ok:
                errors.append(battery_can_health.status_message())

        if self.battery is not None:
            try:
                battery = self.battery.read()
            except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
                errors.append(f"Battery CAN read failed: {exc}")

        return SupervisorSnapshot(
            captured_at=datetime.now(timezone.utc),
            classic=classic,
            classic_settings=classic_settings,
            battery=battery,
            battery_can_health=battery_can_health,
            ambient=ambient,
            errors=errors,
        )
