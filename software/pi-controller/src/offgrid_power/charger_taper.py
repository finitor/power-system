"""Battery-informed charger-current tapering near the LiFePO4 top knee."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .canbus import PylonCanSnapshot


ACTIVE_CHARGE_STAGES = {"Absorb", "BulkMppt", "Float", "FloatMppt"}
STOP_CHARGE_STAGES = {"Equalize", "EqMppt"}


@dataclass(frozen=True)
class ChargerCurrentTaperConfig:
    bulk_soc_percent: float = 85.0
    ramp2_soc_percent: float = 92.0
    full_soc_percent: float = 100.0
    full_reset_soc_percent: float = 98.0
    bulk_voltage_v: float = 53.6
    ramp2_voltage_v: float = 54.4
    top_voltage_v: float = 54.8
    full_reset_voltage_v: float = 54.0
    # Operator ceiling: keep equal to the charger limit configured at the
    # panel. The taper only ever reduces from here; it must never raise the
    # limit above what the operator chose (first caught by dry-run
    # 2026-06-10, when bulk targeted 100A over an 80A panel setting).
    bulk_current_a: float = 80.0
    ramp1_high_current_a: float = 30.0
    ramp1_low_current_a: float = 20.0
    ramp2_high_current_a: float = 10.0
    ramp2_low_current_a: float = 4.0
    high_cell_stop_v: float = 3.55
    high_cell_soft_limit_v: float = 3.50
    high_delta_stop_mv: float = 175.0
    min_write_delta_a: float = 1.0


@dataclass(frozen=True)
class ChargerCurrentTaperDecision:
    target_current_a: float | None
    reason: str
    should_write: bool = False


@dataclass(frozen=True)
class ChargerTelemetry:
    voltage_v: float
    charge_stage: str


@dataclass(frozen=True)
class ChargerCurrentSettings:
    current_limit_a: float


class ChargerCurrentTaperController:
    def __init__(self, config: ChargerCurrentTaperConfig | None = None) -> None:
        self.config = config or ChargerCurrentTaperConfig()
        self._full_latched = False

    def decide(
        self,
        charger: ChargerTelemetry | None,
        settings: ChargerCurrentSettings | None,
        battery: PylonCanSnapshot | None,
    ) -> ChargerCurrentTaperDecision:
        if charger is None or settings is None:
            return ChargerCurrentTaperDecision(None, "missing charger telemetry")
        if battery is None:
            return ChargerCurrentTaperDecision(None, "missing battery telemetry")

        target, reason = self._target_current(charger, battery)
        if target is None:
            return ChargerCurrentTaperDecision(None, reason)

        if battery.charge_limits is not None:
            target = min(target, max(0.0, battery.charge_limits.charge_current_limit_a))

        # Never target above the operator ceiling, whatever the candidates say.
        target = min(target, self.config.bulk_current_a)
        target = round(max(0.0, target), 1)
        should_write = abs(settings.current_limit_a - target) >= self.config.min_write_delta_a
        return ChargerCurrentTaperDecision(target, reason, should_write=should_write)

    def _target_current(
        self,
        charger: ChargerTelemetry,
        battery: PylonCanSnapshot,
    ) -> tuple[float | None, str]:
        config = self.config
        soc = battery.state_of_charge.soc_percent if battery.state_of_charge is not None else None
        voltage = battery.measurements.voltage_v if battery.measurements is not None else charger.voltage_v
        extended = battery.extended_measurements
        max_cell_v = extended.max_cell_voltage_v if extended is not None else None
        min_cell_v = extended.min_cell_voltage_v if extended is not None else None

        if battery.request_flags is not None and not battery.request_flags.charge_enable:
            return 0.0, "BMS charge disabled"
        if battery.charge_limits is not None and battery.charge_limits.charge_current_limit_a <= 0:
            return 0.0, "BMS CCL is zero"
        if charger.charge_stage in STOP_CHARGE_STAGES:
            return 0.0, f"charger stage {charger.charge_stage}"
        if charger.charge_stage not in ACTIVE_CHARGE_STAGES:
            return None, f"charger stage {charger.charge_stage}"
        if max_cell_v is not None and max_cell_v >= config.high_cell_stop_v:
            return 0.0, f"max cell {max_cell_v:.3f}V >= {config.high_cell_stop_v:.3f}V"
        if min_cell_v is not None and max_cell_v is not None:
            delta_mv = (max_cell_v - min_cell_v) * 1000
            if delta_mv >= config.high_delta_stop_mv and max_cell_v >= config.high_cell_soft_limit_v:
                return 0.0, f"cell delta {delta_mv:.0f}mV >= {config.high_delta_stop_mv:.0f}mV"
        if soc is not None and soc >= config.full_soc_percent:
            self._full_latched = True
        if self._full_latched:
            if soc is not None and soc < config.full_reset_soc_percent and voltage <= config.full_reset_voltage_v:
                self._full_latched = False
            else:
                return 0.0, "full-charge latch"

        candidates = []
        if soc is not None:
            candidates.append(_target_from_soc(float(soc), config))
        if voltage is not None:
            candidates.append(_target_from_voltage(float(voltage), config))
        if max_cell_v is not None and max_cell_v >= config.high_cell_soft_limit_v:
            candidates.append(config.ramp2_low_current_a)

        if not candidates:
            return None, "missing SOC and voltage"
        target = min(candidates)
        return target, "dynamic taper"


def _target_from_soc(soc_percent: float, config: ChargerCurrentTaperConfig) -> float:
    if soc_percent >= config.full_soc_percent:
        return 0.0
    if soc_percent >= config.ramp2_soc_percent:
        return _interpolate(
            soc_percent,
            config.ramp2_soc_percent,
            config.ramp2_high_current_a,
            config.full_soc_percent,
            config.ramp2_low_current_a,
        )
    if soc_percent >= config.bulk_soc_percent:
        return _interpolate(
            soc_percent,
            config.bulk_soc_percent,
            config.ramp1_high_current_a,
            config.ramp2_soc_percent,
            config.ramp1_low_current_a,
        )
    return config.bulk_current_a


def _target_from_voltage(voltage_v: float, config: ChargerCurrentTaperConfig) -> float:
    if voltage_v >= config.top_voltage_v:
        return config.ramp2_low_current_a
    if voltage_v >= config.ramp2_voltage_v:
        return _interpolate(
            voltage_v,
            config.ramp2_voltage_v,
            config.ramp2_high_current_a,
            config.top_voltage_v,
            config.ramp2_low_current_a,
        )
    if voltage_v >= config.bulk_voltage_v:
        return _interpolate(
            voltage_v,
            config.bulk_voltage_v,
            config.ramp1_high_current_a,
            config.ramp2_voltage_v,
            config.ramp1_low_current_a,
        )
    return config.bulk_current_a


def _interpolate(value: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x1 == x0:
        return y1
    fraction = min(1.0, max(0.0, (value - x0) / (x1 - x0)))
    return y0 + (y1 - y0) * fraction


TAPER_LOG_FIELDS = [
    "captured_at",
    "mode",
    "charge_stage",
    "battery_voltage_v",
    "current_limit_a",
    "target_current_a",
    "reason",
    "soc_percent",
    "max_cell_v",
    "cell_delta_mv",
]


def append_decision_log(
    path: str,
    *,
    dry_run: bool,
    charge_stage: str | None,
    battery_voltage_v: float | None,
    current_limit_a: float | None,
    decision: ChargerCurrentTaperDecision,
    battery: PylonCanSnapshot | None,
    captured_at: datetime | None = None,
) -> None:
    """Append one actionable taper decision to a durable CSV.

    The dry-run validation record must survive reboots (journald on the Pi
    may be volatile), so decisions land next to the other telemetry logs.
    """
    if not path:
        return
    soc = max_cell_v = delta_mv = None
    if battery is not None:
        if battery.state_of_charge is not None:
            soc = battery.state_of_charge.soc_percent
        extended = battery.extended_measurements
        if extended is not None and extended.max_cell_voltage_v is not None:
            max_cell_v = extended.max_cell_voltage_v
            if extended.min_cell_voltage_v is not None:
                delta_mv = round((extended.max_cell_voltage_v - extended.min_cell_voltage_v) * 1000)

    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not log_path.exists() or log_path.stat().st_size == 0
    with log_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TAPER_LOG_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(
            {
                "captured_at": (captured_at or datetime.now(timezone.utc)).isoformat(),
                "mode": "dry-run" if dry_run else "live",
                "charge_stage": charge_stage or "",
                "battery_voltage_v": "" if battery_voltage_v is None else f"{battery_voltage_v:.2f}",
                "current_limit_a": "" if current_limit_a is None else f"{current_limit_a:.1f}",
                "target_current_a": "" if decision.target_current_a is None else f"{decision.target_current_a:.1f}",
                "reason": decision.reason,
                "soc_percent": "" if soc is None else soc,
                "max_cell_v": "" if max_cell_v is None else f"{max_cell_v:.3f}",
                "cell_delta_mv": "" if delta_mv is None else delta_mv,
            }
        )
