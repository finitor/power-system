"""Read-only supervisory snapshot assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .ambient import AmbientDhtClient, AmbientDs18b20Client, AmbientProbeDisconnected, AmbientTelemetry
from .canbus import BatteryCanClient, PylonCanSnapshot
from .classic import ClassicChargeSettings, ClassicClient, ClassicTelemetry


@dataclass(frozen=True)
class SupervisorSnapshot:
    captured_at: datetime
    classic: ClassicTelemetry | None
    classic_settings: ClassicChargeSettings | None
    battery: PylonCanSnapshot | None
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
    ) -> None:
        self.classic = classic
        self.ambient = ambient
        self.battery = battery

    def read_snapshot(self) -> SupervisorSnapshot:
        errors: list[str] = []
        classic: ClassicTelemetry | None = None
        classic_settings: ClassicChargeSettings | None = None
        battery: PylonCanSnapshot | None = None
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
            ambient=ambient,
            errors=errors,
        )
