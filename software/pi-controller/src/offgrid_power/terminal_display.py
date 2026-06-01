"""Terminal display rendering for the Pi supervisor."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from typing import TYPE_CHECKING

from .supervisor import SupervisorSnapshot

if TYPE_CHECKING:
    from .classic import ClassicChargeSettings
    from .web_display import HouseholdLoadSummary

CHANGED_DIGIT_START = "\033[93m"
CHANGED_DIGIT_END = "\033[0m"
DIRECTION_ARROW_START = "\033[92m"
UP_ARROW = f"{DIRECTION_ARROW_START}↑{CHANGED_DIGIT_END}"
DOWN_ARROW = f"{DIRECTION_ARROW_START}↓{CHANGED_DIGIT_END}"
MEASUREMENT_PATTERN = re.compile(r"(?<![\w-])(-?\d+(?:\.\d+)?)(kWh|mV|Ah|[VAWCs%])(?![\w-])")
ROW_LABEL_WIDTH = 21
BATTERY_IDLE_CURRENT_A = 0.5


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def format_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def highlight_changed_digits(previous: str | None, current: str) -> str:
    if previous is None:
        return current

    value_markers, value_highlights = _value_change_annotations(previous, current)
    highlighted: list[str] = []
    line_index = 0
    column_index = 0
    for index, char in enumerate(current):
        previous_char = previous[index] if index < len(previous) else ""
        highlight_value = (line_index, column_index) in value_highlights
        if highlight_value and (line_index, column_index - 1) not in value_highlights:
            highlighted.append(CHANGED_DIGIT_START)

        if char.isdigit() and char != previous_char and current.startswith("Local time:", index - column_index):
            highlighted.append(f"{CHANGED_DIGIT_START}{char}{CHANGED_DIGIT_END}")
        else:
            highlighted.append(char)

        if highlight_value and (line_index, column_index + 1) not in value_highlights:
            highlighted.append(CHANGED_DIGIT_END)

        marker = value_markers.get((line_index, column_index))
        if marker is not None:
            highlighted.append(marker)

        if char == "\n":
            line_index += 1
            column_index = 0
        else:
            column_index += 1
    return "".join(highlighted)


def _value_change_annotations(previous: str, current: str) -> tuple[dict[tuple[int, int], str], set[tuple[int, int]]]:
    markers: dict[tuple[int, int], str] = {}
    highlights: set[tuple[int, int]] = set()
    previous_lines = previous.splitlines()

    for line_index, current_line in enumerate(current.splitlines()):
        if current_line.startswith("Local time:") or line_index >= len(previous_lines):
            continue

        previous_values = list(MEASUREMENT_PATTERN.finditer(previous_lines[line_index]))
        current_values = list(MEASUREMENT_PATTERN.finditer(current_line))
        for previous_match, current_match in zip(previous_values, current_values, strict=False):
            previous_value = float(previous_match.group(1))
            current_value = float(current_match.group(1))
            if current_value > previous_value:
                markers[(line_index, current_match.end() - 1)] = UP_ARROW
                highlights.update((line_index, column) for column in range(current_match.start(), current_match.end()))
            elif current_value < previous_value:
                markers[(line_index, current_match.end() - 1)] = DOWN_ARROW
                highlights.update((line_index, column) for column in range(current_match.start(), current_match.end()))
            else:
                markers[(line_index, current_match.end() - 1)] = " "

    return markers, highlights


def render_snapshot(snapshot: SupervisorSnapshot, household_load: HouseholdLoadSummary | None = None) -> str:
    lines: list[str] = []
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    lines.append("Off-Grid Power Supervisor".ljust(width))
    lines.append(f"Local time: {format_time(snapshot.captured_at)}")
    lines.append(_status_line(snapshot))
    lines.append("")

    lines.append("Load")
    if household_load is None:
        lines.append("  No data")
    else:
        lines.append(_row("Now", f"{household_load.current_a:.1f}A  {household_load.power_w}W"))
        if household_load.average_today_text is not None:
            lines.append(_row("Average Today", household_load.average_today_text))
        if household_load.today_text is not None:
            lines.append(_row("Cumulative Today", household_load.today_text))
        if household_load.remaining_text is not None:
            lines.append(_row("Estimated Autonomy", household_load.remaining_text))
    lines.append("")

    lines.extend(_battery_bank_lines(snapshot))

    lines.append("")
    lines.extend(_charge_controller_lines(snapshot))

    lines.append("")
    lines.append("Temperature Probes")
    if snapshot.ambient is None:
        lines.append(_row("Sensor 0 ambient temp", "disconnected"))
    else:
        ambient = snapshot.ambient
        lines.append(_row("Sensor 0 ambient temp", f"{ambient.temperature_c:.1f}C"))
        if ambient.humidity_percent is not None:
            lines.append(_row("Humidity", f"{ambient.humidity_percent:.1f}%"))

    if snapshot.errors:
        lines.append("")
        lines.append("Errors")
        for error in snapshot.errors:
            lines.append(f"  - {error}")

    lines.append("")
    lines.append("Press Ctrl-C to exit. Read-only monitor; no control writes are performed.")
    return "\n".join(lines)


def _charge_controller_lines(snapshot: SupervisorSnapshot) -> list[str]:
    lines: list[str] = []
    controllers = [(0, snapshot.classic)]
    for index, classic in controllers:
        lines.append(f"Charge Controller {index}")
        if classic is None:
            lines.append("  No data")
            continue

        lines.append(_row("PV", f"{classic.pv_voltage_v:.1f}V  {classic.pv_current_a:.1f}A"))
        lines.append(_row("Battery", f"{classic.battery_voltage_v:.1f}V  {classic.battery_current_a:.1f}A  {classic.battery_power_w}W"))
        stage_value = classic.charge_stage
        if classic.state != classic.charge_stage:
            stage_value += f"  State: {classic.state}"
        lines.append(_row("Stage", stage_value))
        lines.append(_row("Today Cumulative", f"{classic.daily_energy_kwh:.1f}kWh  {classic.daily_amp_hours_ah}Ah"))
        lines.append(_row("Temps", f"batt {classic.battery_temp_c:.1f}C  FET {classic.fet_temp_c:.1f}C  PCB {classic.pcb_temp_c:.1f}C"))
        if index == 0 and snapshot.classic_settings is not None:
            lines.append(_charge_settings_line(snapshot.classic_settings))
    return lines


def _charge_settings_line(settings: ClassicChargeSettings) -> str:
    return _row(
        "Charge Settings",
        f"Limit {settings.battery_current_limit_a:.1f}A  "
        f"Absorb {settings.absorb_voltage_v:.1f}V for {settings.absorb_time_s}s  "
        f"Float {settings.float_voltage_v:.1f}V  "
        f"EQ {settings.equalize_voltage_v:.1f}V",
    )


def _status_line(snapshot: SupervisorSnapshot) -> str:
    status = f"Status:  {'OK' if snapshot.ok else 'ERROR'}"
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return status
    return f"SOC: {snapshot.battery.state_of_charge.soc_percent:3d}%  {status}"


def _battery_bank_lines(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["Battery Bank"]
    if snapshot.battery is None:
        lines.append("  No CAN data")
        return lines

    battery = snapshot.battery
    measurements = battery.measurements
    limits = battery.charge_limits
    status = battery.status
    requests = battery.request_flags
    extended = battery.extended_measurements

    if measurements is not None:
        lines.append(_row("Pack", f"{measurements.voltage_v:.2f}V  {measurements.current_a:.1f}A  {_battery_state(measurements.current_a)}"))
    if limits is not None:
        lines.append(_row("Limits", f"charge {limits.charge_voltage_limit_v:.1f}V/{limits.charge_current_limit_a:.1f}A  discharge {limits.discharge_current_limit_a:.1f}A"))
    if requests is not None:
        charge = "yes" if requests.charge_enable else "no"
        discharge = "yes" if requests.discharge_enable else "no"
        extra_requests = []
        if requests.force_charge_1 or requests.force_charge_2:
            extra_requests.append("force charge")
        if requests.full_charge_request:
            extra_requests.append("full charge")
        suffix = f"  Request: {', '.join(extra_requests)}" if extra_requests else ""
        lines.append(_row("Enable", f"charge {charge}  discharge {discharge}{suffix}"))
    if extended is not None and extended.min_cell_voltage_v is not None and extended.max_cell_voltage_v is not None:
        delta_mv = round((extended.max_cell_voltage_v - extended.min_cell_voltage_v) * 1000)
        line = f"{extended.min_cell_voltage_v:.3f}-{extended.max_cell_voltage_v:.3f}V ({delta_mv}mV delta)"
        if extended.min_cell_temperature_c is not None and extended.max_cell_temperature_c is not None:
            line += f"  {extended.min_cell_temperature_c:.1f}-{extended.max_cell_temperature_c:.1f}C"
        lines.append(_row("Cells", line))
    if status is not None:
        conditions = [*status.protection_flags, *status.alarm_flags]
        value = "none" if not conditions else ", ".join(conditions)
        lines.append(_row("Protection/Alarms", value))
    return lines


def _row(label: str, value: str) -> str:
    return f"  {label + ':':<{ROW_LABEL_WIDTH + 1}} {value}"


def _battery_state(current_a: float) -> str:
    if current_a > BATTERY_IDLE_CURRENT_A:
        return "charging"
    if current_a < -BATTERY_IDLE_CURRENT_A:
        return "discharging"
    return "idle"
