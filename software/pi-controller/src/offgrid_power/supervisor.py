"""Read-only supervisory snapshot assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .ambient import AmbientDhtClient, AmbientDs18b20Client, AmbientProbeDisconnected, AmbientTelemetry
from .canbus import BatteryCanClient, CanBusHealth, PylonCanSnapshot, canbus_health
from .classic import ClassicChargeSettings, ClassicClient, ClassicTelemetry


STATUS_OK = "OK"
STATUS_WARNING = "WARNING"
STATUS_ERROR = "ERROR"

CELL_HIGH_VOLTAGE_WARNING_V = 3.55
CELL_OVERVOLTAGE_ALERT_V = 3.60
CELL_DELTA_TOP_OF_CHARGE_V = 3.45
CELL_DELTA_WARNING_MV = 75
CELL_DELTA_CRITICAL_MV = 100


@dataclass(frozen=True)
class SupervisorSnapshot:
    captured_at: datetime
    classic: ClassicTelemetry | None
    classic_settings: ClassicChargeSettings | None
    battery: PylonCanSnapshot | None
    battery_can_health: CanBusHealth | None
    ambient: AmbientTelemetry | None
    errors: list[str]
    status_conditions: list[str] = field(default_factory=list)
    status_severity: str = STATUS_OK

    def __post_init__(self) -> None:
        if self.status_conditions and self.status_severity == STATUS_OK:
            object.__setattr__(self, "status_severity", STATUS_WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors and self.status_severity != STATUS_ERROR

    @property
    def status_text(self) -> str:
        if self.errors:
            return STATUS_ERROR
        return self.status_severity


@dataclass(frozen=True)
class StatusConditionCandidate:
    key: str
    text: str
    required_samples: int = 1
    severity: str = STATUS_WARNING


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
        self._status_condition_counts: dict[str, int] = {}

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
            except Exception:  # noqa: BLE001 - ambient is advisory unless a control loop depends on it.
                ambient = None

        if self.battery_can_interface is not None:
            battery_can_health = canbus_health(self.battery_can_interface)
            if not battery_can_health.ok:
                errors.append(battery_can_health.status_message())

        if self.battery is not None:
            try:
                battery = self.battery.read()
            except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
                errors.append(f"Battery CAN read failed: {exc}")

        status_condition_candidates = charge_limit_status_condition_candidates(classic_settings, battery)
        status_condition_candidates.extend(self._stable_status_condition_candidates(cell_status_condition_candidates(battery)))
        status_conditions = [candidate.text for candidate in status_condition_candidates]
        status_severity = status_condition_severity(status_condition_candidates)

        return SupervisorSnapshot(
            captured_at=datetime.now(timezone.utc),
            classic=classic,
            classic_settings=classic_settings,
            battery=battery,
            battery_can_health=battery_can_health,
            ambient=ambient,
            errors=errors,
            status_conditions=status_conditions,
            status_severity=status_severity,
        )

    def _stable_status_condition_candidates(self, candidates: list[StatusConditionCandidate]) -> list[StatusConditionCandidate]:
        active_keys = {candidate.key for candidate in candidates}
        self._status_condition_counts = {
            key: count
            for key, count in self._status_condition_counts.items()
            if key in active_keys
        }

        conditions: list[str] = []
        for candidate in candidates:
            count = self._status_condition_counts.get(candidate.key, 0) + 1
            self._status_condition_counts[candidate.key] = count
            if count >= candidate.required_samples:
                conditions.append(candidate)
        return conditions


def charge_limit_status_conditions(
    classic_settings: ClassicChargeSettings | None,
    battery: PylonCanSnapshot | None,
) -> list[str]:
    return [candidate.text for candidate in charge_limit_status_condition_candidates(classic_settings, battery)]


def charge_limit_status_condition_candidates(
    classic_settings: ClassicChargeSettings | None,
    battery: PylonCanSnapshot | None,
) -> list[StatusConditionCandidate]:
    if classic_settings is None or battery is None or battery.charge_limits is None:
        return []

    conditions: list[StatusConditionCandidate] = []
    limits = battery.charge_limits
    if classic_settings.battery_current_limit_a > limits.charge_current_limit_a:
        conditions.append(
            StatusConditionCandidate(
                "classic.0.ccl_exceeds_bms",
                "Charge controller 0 CCL exceeds battery CCL: "
                f"{classic_settings.battery_current_limit_a:.1f}A > {limits.charge_current_limit_a:.1f}A",
            )
        )

    voltage_setpoints = [
        ("Absorb", classic_settings.absorb_voltage_v),
        ("Float", classic_settings.float_voltage_v),
        ("Equalize", classic_settings.equalize_voltage_v),
        ("Max temp-comp", classic_settings.max_temp_comp_voltage_v),
    ]
    exceeded = [
        f"{label} {value:.1f}V"
        for label, value in voltage_setpoints
        if value > limits.charge_voltage_limit_v
    ]
    if exceeded:
        conditions.append(
            StatusConditionCandidate(
                "classic.0.cvs_exceeds_bms",
                "Charge controller 0 CVS exceeds battery CVL: "
                f"{', '.join(exceeded)} > {limits.charge_voltage_limit_v:.1f}V",
                severity=STATUS_ERROR,
            )
        )
    return conditions


def status_condition_severity(candidates: list[StatusConditionCandidate]) -> str:
    if any(candidate.severity == STATUS_ERROR for candidate in candidates):
        return STATUS_ERROR
    if candidates:
        return STATUS_WARNING
    return STATUS_OK


def cell_status_condition_candidates(battery: PylonCanSnapshot | None) -> list[StatusConditionCandidate]:
    if battery is None or battery.extended_measurements is None:
        return []

    extended = battery.extended_measurements
    candidates: list[StatusConditionCandidate] = []
    max_cell_v = extended.max_cell_voltage_v
    min_cell_v = extended.min_cell_voltage_v

    if max_cell_v is not None:
        if max_cell_v >= CELL_OVERVOLTAGE_ALERT_V:
            candidates.append(
                StatusConditionCandidate(
                    "battery.cell.overvoltage",
                    f"Battery cell overvoltage risk: max cell {max_cell_v:.3f}V >= {CELL_OVERVOLTAGE_ALERT_V:.3f}V",
                    severity=STATUS_ERROR,
                )
            )
        elif max_cell_v >= CELL_HIGH_VOLTAGE_WARNING_V:
            candidates.append(
                StatusConditionCandidate(
                    "battery.cell.high",
                    f"Battery cell high: max cell {max_cell_v:.3f}V >= {CELL_HIGH_VOLTAGE_WARNING_V:.3f}V",
                    required_samples=2,
                )
            )

    if min_cell_v is not None and max_cell_v is not None and max_cell_v >= CELL_DELTA_TOP_OF_CHARGE_V:
        delta_mv = round((max_cell_v - min_cell_v) * 1000)
        if delta_mv >= CELL_DELTA_CRITICAL_MV:
            candidates.append(
                StatusConditionCandidate(
                    "battery.cell.delta.critical",
                    "Battery cell delta critical: "
                    f"{delta_mv}mV >= {CELL_DELTA_CRITICAL_MV}mV while max cell {max_cell_v:.3f}V >= {CELL_DELTA_TOP_OF_CHARGE_V:.3f}V",
                    required_samples=2,
                )
            )
        elif delta_mv >= CELL_DELTA_WARNING_MV:
            candidates.append(
                StatusConditionCandidate(
                    "battery.cell.delta.high",
                    "Battery cell delta high: "
                    f"{delta_mv}mV >= {CELL_DELTA_WARNING_MV}mV while max cell {max_cell_v:.3f}V >= {CELL_DELTA_TOP_OF_CHARGE_V:.3f}V",
                    required_samples=2,
                )
            )
    return candidates
