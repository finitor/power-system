"""Read-only supervisory snapshot assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .classic import ClassicChargeSettings, ClassicClient, ClassicTelemetry


@dataclass(frozen=True)
class SupervisorSnapshot:
    captured_at: datetime
    classic: ClassicTelemetry | None
    classic_settings: ClassicChargeSettings | None
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


class Supervisor:
    def __init__(self, classic: ClassicClient) -> None:
        self.classic = classic

    def read_snapshot(self) -> SupervisorSnapshot:
        errors: list[str] = []
        classic: ClassicTelemetry | None = None
        classic_settings: ClassicChargeSettings | None = None

        try:
            classic, classic_settings = self.classic.read()
        except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
            errors.append(f"Classic read failed: {exc}")

        return SupervisorSnapshot(
            captured_at=datetime.now(timezone.utc),
            classic=classic,
            classic_settings=classic_settings,
            errors=errors,
        )

