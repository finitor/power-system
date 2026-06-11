"""Terminal rendering for supervisor API snapshots."""

from __future__ import annotations

from datetime import datetime
import shutil

from .terminal_display import format_cell_location_for_display, format_updated_time


ROW_LABEL_WIDTH = 21


def render_api_snapshot(payload: dict, now: datetime | None = None) -> str:
    lines: list[str] = []
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    captured_at = _parse_datetime(payload.get("captured_at"))
    status = payload.get("status") or {}
    battery = payload.get("battery") or {}

    lines.append("Off-Grid Power Supervisor".ljust(width))
    if captured_at is None:
        lines.append("Updated: unavailable")
    else:
        lines.append(f"Updated: {format_updated_time(captured_at)}")
    lines.append(_status_line(status, battery))
    lines.append("")

    lines.append("Load")
    load = payload.get("load")
    if load is None:
        lines.append("  No data")
    else:
        lines.append(_row("Now", f"{_fmt(load.get('current_a'), 1)}A  {_fmt(load.get('power_w'), 0)}W"))
        if load.get("average_today_text") is not None:
            lines.append(_row("3hr Rolling Avg", str(load["average_today_text"])))
        if load.get("today_text") is not None:
            lines.append(_row("Cumulative Today", str(load["today_text"])))
        if load.get("remaining_text") is not None:
            lines.append(_row("Estimated Autonomy", str(load["remaining_text"])))

    lines.append("")
    lines.extend(_battery_lines(battery if payload.get("battery") is not None else None))

    lines.append("")
    lines.extend(_solar_lines(payload.get("solar") or []))

    lines.append("")
    lines.extend(_temperature_lines(payload))

    errors = status.get("errors") or []
    conditions = status.get("conditions") or []
    if errors:
        lines.append("")
        lines.append("Errors")
        for error in errors:
            lines.append(f"  - {error}")
    if conditions:
        lines.append("")
        lines.append("Status Conditions")
        for condition in conditions:
            lines.append(f"  - {condition}")

    return "\n".join(lines)


def render_api_unavailable(error: str) -> str:
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    return "\n".join(
        [
            "Off-Grid Power Supervisor".ljust(width),
            "Updated: unavailable",
            "Status:  UNAVAILABLE",
            "",
            "API snapshot unavailable",
            f"  - {error}",
        ]
    )


def _status_line(status: dict, battery: dict) -> str:
    severity = status.get("severity") or status.get("status") or "UNKNOWN"
    soc = battery.get("soc_percent")
    if soc is None:
        return f"SOC:  --  Status:  {severity}"
    return f"SOC: {int(soc):3d}%  Status:  {severity}"


def _battery_lines(battery: dict | None) -> list[str]:
    lines = ["Battery Bank"]
    if battery is None:
        lines.append("  No data")
        return lines

    voltage = battery.get("voltage_v")
    current = battery.get("current_a")
    power = battery.get("power_w")
    if voltage is not None and current is not None and power is not None:
        lines.append(_row("Flow", f"{_fmt(voltage, 2)}V  {_fmt(current, 1)}A  {_fmt(power, 0)}W  {_battery_state(float(current))}"))

    min_cell = battery.get("cell_min_v")
    max_cell = battery.get("cell_max_v")
    delta = battery.get("cell_delta_mv")
    if min_cell is not None and max_cell is not None and delta is not None:
        min_location = format_cell_location_for_display(battery.get("cell_min_location"))
        max_location = format_cell_location_for_display(battery.get("cell_max_location"))
        value = f"Δ {_fmt(delta, 0)}mV"
        value += f"; min {min_location or '?'} {_fmt(min_cell, 3)}V"
        value += f"; max {max_location or '?'} {_fmt(max_cell, 3)}V"
        lines.append(_row("Cells", value))

    protections = [*(battery.get("protection_flags") or []), *(battery.get("alarm_flags") or [])]
    lines.append(_row("Protection/Alarms", "none" if not protections else ", ".join(protections)))

    charge = _yes_no(battery.get("charge_enabled"))
    discharge = _yes_no(battery.get("discharge_enabled"))
    if charge is not None or discharge is not None:
        lines.append(_row("Enable", f"charge {charge or 'unknown'}  discharge {discharge or 'unknown'}"))

    cvl = battery.get("charge_voltage_limit_v")
    ccl = battery.get("charge_current_limit_a")
    dcl = battery.get("discharge_current_limit_a")
    if cvl is not None and ccl is not None and dcl is not None:
        lines.append(_row("Limits", f"charge {_fmt(cvl, 1)}V/{_fmt(ccl, 1)}A  discharge {_fmt(dcl, 1)}A"))
    return lines


def _solar_lines(solar: list[dict]) -> list[str]:
    lines: list[str] = []
    if not solar:
        return ["Charge Controller 0 (Classic)", "  No data"]
    for index, controller in enumerate(solar):
        if lines:
            lines.append("")
        lines.append(_charge_controller_title(index, controller))
        pv_parts = [
            f"{_fmt(controller.get('pv_voltage_v'), 1)}V",
            f"{_fmt(controller.get('pv_current_a'), 1)}A",
        ]
        if controller.get("last_voc_v") is not None:
            pv_parts.append(f"Voc {_fmt(controller.get('last_voc_v'), 1)}V")
        elif controller.get("pv_power_w") is not None:
            pv_parts.append(f"{_fmt(controller.get('pv_power_w'), 0)}W")
        lines.append(
            _row(
                "PV",
                "  ".join(pv_parts),
            )
        )
        lines.append(
            _row(
                "Output",
                f"{_fmt(controller.get('battery_voltage_v'), 1)}V  "
                f"{_fmt(controller.get('battery_current_a'), 1)}A  "
                f"{_fmt(controller.get('battery_power_w'), 0)}W",
            )
        )
        stage = controller.get("charge_stage")
        state = controller.get("state")
        stage_value = f"Stage: {stage or 'unknown'}"
        if state is not None and state != stage:
            stage_value += f"  State: {state}"
        lines.append(_row("Charge Status", stage_value))
        if controller.get("daily_energy_kwh") is not None or controller.get("daily_amp_hours_ah") is not None:
            lines.append(
                _row(
                    "Production Today",
                    f"{_fmt(controller.get('daily_energy_kwh'), 1)}kWh  "
                    f"{_fmt(controller.get('daily_amp_hours_ah'), 0)}Ah",
                )
            )
        if controller.get("rated_pv_voltage_v") is not None or controller.get("rated_charging_current_a") is not None:
            lines.append(
                _row(
                    "Rated",
                    f"{_fmt(controller.get('rated_pv_voltage_v'), 0)}V PV  "
                    f"{_fmt(controller.get('rated_charging_current_a'), 0)}A charge",
                )
            )
        settings = controller.get("settings")
        if settings is not None:
            if "current_limit_a" in settings:
                value = (
                    f"Limit {_fmt(settings.get('current_limit_a'), 1)}A  "
                    f"Absorb {_fmt(settings.get('absorb_voltage_v'), 1)}V {_fmt(_hours(settings.get('absorb_time_s')), 1)}h  "
                    f"Float {_fmt(settings.get('float_voltage_v'), 1)}V  "
                    f"EQ {_fmt(settings.get('equalize_voltage_v'), 1)}V"
                )
            else:
                value = (
                    f"Type {settings.get('battery_type') or 'unknown'}  "
                    f"Boost {_fmt(settings.get('boost_voltage_v'), 1)}V  "
                    f"Float {_fmt(settings.get('float_voltage_v'), 1)}V  "
                    f"LVD {_fmt(settings.get('low_voltage_disconnect_v'), 1)}V"
                )
            lines.append(_row("Charge Settings", value))
    return lines


def _charge_controller_title(index: int, controller: dict) -> str:
    controller_id = controller.get("id")
    if controller_id == "classic.0":
        return f"Charge Controller {index} (Classic)"
    if controller_id == "epever.1":
        return f"Charge Controller {index} (Epever)"
    return f"Charge Controller {index}"


def _temperature_lines(payload: dict) -> list[str]:
    lines = ["Temperatures"]
    battery = payload.get("battery") or {}
    ambient = payload.get("ambient") or {}
    solar = payload.get("solar") or []
    cell_min = battery.get("cell_temperature_min_c")
    cell_max = battery.get("cell_temperature_max_c")
    if cell_min is not None and cell_max is not None:
        lines.append(_row("Battery cells", f"{_fmt(cell_min, 1)}-{_fmt(cell_max, 1)}C"))
    for index, controller in enumerate(solar):
        temps = controller.get("temperatures_c") or {}
        prefix = f"CC{index}"
        if temps.get("battery") is not None:
            label = "Battery terminal" if index == 0 else f"{prefix} battery"
            lines.append(_row(label, f"{_fmt(temps.get('battery'), 1)}C"))
        if temps.get("fet") is not None:
            lines.append(_row(f"{prefix} FET", f"{_fmt(temps.get('fet'), 1)}C"))
        if temps.get("pcb") is not None:
            lines.append(_row(f"{prefix} PCB", f"{_fmt(temps.get('pcb'), 1)}C"))
        if temps.get("device") is not None:
            lines.append(_row(f"{prefix} device", f"{_fmt(temps.get('device'), 1)}C"))
    if ambient.get("temperature_c") is None:
        lines.append(_row("Sensor 0 ambient temp", "disconnected"))
    else:
        lines.append(_row("Sensor 0 ambient temp", f"{_fmt(ambient.get('temperature_c'), 1)}C"))
        if ambient.get("humidity_percent") is not None:
            lines.append(_row("Humidity", f"{_fmt(ambient.get('humidity_percent'), 1)}%"))
    return lines


def _hours(seconds) -> float | None:
    try:
        return float(seconds) / 3600
    except (TypeError, ValueError):
        return None


def _row(label: str, value: str) -> str:
    return f"  {label:<{ROW_LABEL_WIDTH}} {value}"


def _fmt(value, decimals: int) -> str:
    if value is None:
        return "?"
    return f"{float(value):.{decimals}f}"


def _yes_no(value) -> str | None:
    if value is None:
        return None
    return "yes" if value else "no"


def _battery_state(current_a: float) -> str:
    if current_a > 0.5:
        return "charging"
    if current_a < -0.5:
        return "discharging"
    return "idle"


def _parse_datetime(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
