"""Terminal display rendering for the Pi supervisor."""

from __future__ import annotations

import re
import shutil
from datetime import datetime

from .supervisor import SupervisorSnapshot

CHANGED_DIGIT_START = "\033[93m"
CHANGED_DIGIT_END = "\033[0m"
DIRECTION_ARROW_START = "\033[92m"
UP_ARROW = f"{DIRECTION_ARROW_START}↑{CHANGED_DIGIT_END}"
DOWN_ARROW = f"{DIRECTION_ARROW_START}↓{CHANGED_DIGIT_END}"
MEASUREMENT_PATTERN = re.compile(r"(?<![\w-])(-?\d+(?:\.\d+)?)(kWh|Ah|[VAWCs%])(?![\w-])")


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


def render_snapshot(snapshot: SupervisorSnapshot) -> str:
    lines: list[str] = []
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    lines.append("Off-Grid Power Supervisor".ljust(width))
    lines.append(f"Local time: {format_time(snapshot.captured_at)}")
    lines.append(f"Status:  {'OK' if snapshot.ok else 'ERROR'}")
    lines.append("")

    lines.append("Battery Bank")
    if snapshot.battery is None:
        if snapshot.battery_can_health is None:
            lines.append("  No CAN data")
        elif snapshot.battery_can_health.dfu_devices:
            lines.append("  CAN adapter: DFU/bootloader mode")
            for device in snapshot.battery_can_health.dfu_devices[:2]:
                product = device.product or "STM32 DFU"
                serial = f" serial {device.serial}" if device.serial else ""
                lines.append(f"    - {product}{serial}")
            lines.append("  Action: replug USB-CAN adapter without BOOT/DFU pressed")
        elif not snapshot.battery_can_health.socketcan_present:
            lines.append(f"  CAN adapter: interface {snapshot.battery_can_health.interface} not present")
        else:
            lines.append("  No CAN frames received")
    else:
        battery = snapshot.battery
        state = battery.state_of_charge
        measurements = battery.measurements
        limits = battery.charge_limits
        status = battery.status
        requests = battery.request_flags
        extended = battery.extended_measurements

        if measurements is not None:
            lines.append(
                f"  Pack:    {measurements.voltage_v:5.2f}V  "
                f"{measurements.current_a:5.1f}A  {measurements.temperature_c:4.1f}C"
            )
        if state is not None:
            lines.append(f"  State:   SOC {state.soc_percent:3d}%  SOH {state.soh_percent:3d}%")
        if limits is not None:
            lines.append(
                f"  Limits:  charge {limits.charge_voltage_limit_v:4.1f}V/{limits.charge_current_limit_a:5.1f}A  "
                f"discharge {limits.discharge_current_limit_a:5.1f}A"
            )
        if requests is not None:
            charge = "yes" if requests.charge_enable else "no"
            discharge = "yes" if requests.discharge_enable else "no"
            extra_requests = []
            if requests.force_charge_1 or requests.force_charge_2:
                extra_requests.append("force charge")
            if requests.full_charge_request:
                extra_requests.append("full charge")
            suffix = f"  Request: {', '.join(extra_requests)}" if extra_requests else ""
            lines.append(f"  Enable:  charge {charge}  discharge {discharge}{suffix}")
        if status is not None:
            protections = "none" if not status.protection_flags else ", ".join(status.protection_flags[:2])
            alarms = "none" if not status.alarm_flags else ", ".join(status.alarm_flags[:2])
            lines.append(f"  BMS:     modules {status.module_count}  protect {protections}  alarms {alarms}")
        if extended is not None and extended.min_cell_voltage_v is not None and extended.max_cell_voltage_v is not None:
            line = f"  Cells:   {extended.min_cell_voltage_v:.3f}-{extended.max_cell_voltage_v:.3f}V"
            if extended.min_cell_temperature_c is not None and extended.max_cell_temperature_c is not None:
                line += f"  {extended.min_cell_temperature_c:4.1f}-{extended.max_cell_temperature_c:4.1f}C"
            lines.append(line)

    lines.append("")
    if snapshot.classic is None:
        lines.append("Charge Controller")
        lines.append("  No data")
    else:
        classic = snapshot.classic
        lines.append("Charge Controller")
        lines.append(f"  Battery: {classic.battery_voltage_v:5.1f}V  {classic.battery_current_a:5.1f}A  {classic.battery_power_w:5d}W")
        lines.append(f"  PV:      {classic.pv_voltage_v:5.1f}V  {classic.pv_current_a:5.1f}A")
        lines.append(f"  Stage:   {classic.charge_stage}  State: {classic.state}")
        lines.append(f"  Today:   {classic.daily_energy_kwh:5.1f}kWh  {classic.daily_amp_hours_ah:5d}Ah")

    lines.append("")
    lines.append("Temperatures")
    if snapshot.classic is not None:
        classic = snapshot.classic
        lines.append(f"  Battery terminal: {classic.battery_temp_c:5.1f}C")
        lines.append(f"  Charge controller FET: {classic.fet_temp_c:5.1f}C")
        lines.append(f"  Charge controller PCB: {classic.pcb_temp_c:5.1f}C")
    if snapshot.ambient is None:
        lines.append("  Sensor 0 ambient temp: disconnected")
    else:
        ambient = snapshot.ambient
        lines.append(f"  Sensor 0 ambient temp: {ambient.temperature_c:5.1f}C")
        if ambient.humidity_percent is not None:
            lines.append(f"  Humidity:{ambient.humidity_percent:5.1f}%")

    if snapshot.errors:
        lines.append("")
        lines.append("Errors")
        for error in snapshot.errors:
            lines.append(f"  - {error}")

    lines.append("")
    lines.append("Press Ctrl-C to exit. Read-only monitor; no control writes are performed.")
    return "\n".join(lines)
