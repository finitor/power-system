"""Terminal display rendering for the Pi supervisor."""

from __future__ import annotations

import re
import shutil
from datetime import datetime

from .household import HouseholdUsage
from .supervisor import SupervisorSnapshot

CHANGED_DIGIT_START = "\033[93m"
CHANGED_DIGIT_END = "\033[0m"
DIRECTION_ARROW_START = "\033[92m"
UP_ARROW = f"{DIRECTION_ARROW_START}↑{CHANGED_DIGIT_END}"
DOWN_ARROW = f"{DIRECTION_ARROW_START}↓{CHANGED_DIGIT_END}"
MEASUREMENT_PATTERN = re.compile(r"(?<![\w-])(-?\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)(kWh|Ah|[VAWCs%])(?![\w-])")


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def format_refresh_age(captured_at: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(captured_at.tzinfo)
    seconds = max(0, int((now - captured_at).total_seconds()))
    return f"{seconds:02d} seconds ago"


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
        if current_line.startswith("Refreshed:") or line_index >= len(previous_lines):
            continue

        previous_values = list(MEASUREMENT_PATTERN.finditer(previous_lines[line_index]))
        current_values = list(MEASUREMENT_PATTERN.finditer(current_line))
        for previous_match, current_match in zip(previous_values, current_values, strict=False):
            previous_value = _measurement_sort_value(previous_match.group(1))
            current_value = _measurement_sort_value(current_match.group(1))
            if current_value > previous_value:
                markers[(line_index, current_match.end() - 1)] = UP_ARROW
                highlights.update((line_index, column) for column in range(current_match.start(), current_match.end()))
            elif current_value < previous_value:
                markers[(line_index, current_match.end() - 1)] = DOWN_ARROW
                highlights.update((line_index, column) for column in range(current_match.start(), current_match.end()))
            else:
                markers[(line_index, current_match.end() - 1)] = " "

    return markers, highlights


def _measurement_sort_value(value: str) -> tuple[float, ...]:
    if "-" in value[1:]:
        first, second = value.split("-", maxsplit=1)
        return (float(first), float(second))
    return (float(value),)


def render_snapshot(
    snapshot: SupervisorSnapshot,
    now: datetime | None = None,
    household_usage: HouseholdUsage | None = None,
) -> str:
    lines: list[str] = []
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    lines.append("Off-Grid Power Supervisor".ljust(width))
    lines.append(f"Refreshed: {format_refresh_age(snapshot.captured_at, now)}")
    lines.append(f"Status:  {'OK' if snapshot.ok else 'ERROR'}")
    lines.append("")

    lines.append("Household Usage")
    if household_usage is None:
        lines.append("  No data")
    else:
        lines.append(f"  Load:    {household_usage.current_a:5.1f}A  {household_usage.power_w:5.0f}W")
        lines.append(f"  Today:   {household_usage.consumed_ah:5.1f}Ah  {household_usage.consumed_percent:4.1f}% of bank")

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
        requests = battery.request_flags
        extended = battery.extended_measurements

        if measurements is not None:
            lines.append(
                f"  Pack:    {measurements.voltage_v:5.2f}V  "
                f"{measurements.current_a:5.1f}A  {measurements.temperature_c:4.1f}C"
            )
        if state is not None:
            lines.append(f"  State:   SOC {state.soc_percent:3d}%")
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
        if extended is not None and extended.min_cell_voltage_v is not None and extended.max_cell_voltage_v is not None:
            lines.append(f"  Cells:   {extended.min_cell_voltage_v:.3f}-{extended.max_cell_voltage_v:.3f}V")

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
        if classic.is_hypervoc:
            lines.append(
                "  PV input: HyperVOC protection"
                f"  Last Voc {classic.last_voc_v:.1f}V"
                f"  High {classic.highest_input_voltage_v:.1f}V"
            )
        lines.append(f"  Today:   {classic.daily_energy_kwh:5.1f}kWh  {classic.daily_amp_hours_ah:5d}Ah")

    lines.append("")
    lines.append("Temperatures")
    if (
        snapshot.battery is not None
        and snapshot.battery.extended_measurements is not None
        and snapshot.battery.extended_measurements.min_cell_temperature_c is not None
        and snapshot.battery.extended_measurements.max_cell_temperature_c is not None
    ):
        extended = snapshot.battery.extended_measurements
        lines.append(f"  Battery cells: {extended.min_cell_temperature_c:5.1f}-{extended.max_cell_temperature_c:4.1f}C")
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
