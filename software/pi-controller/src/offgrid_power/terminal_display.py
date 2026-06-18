"""Terminal display rendering for the Pi supervisor."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from typing import TYPE_CHECKING

from .load import LoadTotals
from .supervisor import SupervisorSnapshot

if TYPE_CHECKING:
    from .classic import ClassicChargeSettings
    from .epever import EpeverChargeSettings
    from .load import LoadSummary

CHANGED_DIGIT_START = "\033[93m"
CHANGED_DIGIT_END = "\033[0m"
DIRECTION_ARROW_START = "\033[92m"
UP_ARROW = f"{DIRECTION_ARROW_START}↑{CHANGED_DIGIT_END}"
DOWN_ARROW = f"{DIRECTION_ARROW_START}↓{CHANGED_DIGIT_END}"
MEASUREMENT_PATTERN = re.compile(r"(?<![\w-])(-?\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)(kWh|mV|Ah|[VAWCs%])(?![\w-])")
ROW_LABEL_WIDTH = 21
BATTERY_IDLE_CURRENT_A = 0.5


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def format_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def format_updated_time(captured_at: datetime) -> str:
    """Match the web display's Updated stamp, e.g. '18:46:21 EDT'."""
    return captured_at.astimezone().strftime("%H:%M:%S %Z")


def format_cell_location_for_display(location: str | None) -> str:
    if location is None:
        return "?"
    pack_text, separator, cell_text = location.partition(":")
    if not separator:
        return location
    try:
        # Hyphen, not a pipe: "1|16" reads like the numeral run "1116" on the
        # low-res wall console; "1-16" keeps bank and cell legibly distinct.
        return f"{int(pack_text)}-{int(cell_text)}"
    except ValueError:
        return location


def highlight_changed_digits(previous: str | None, current: str) -> str:
    if previous is None:
        return current

    value_markers, value_highlights = _value_change_annotations(previous, current)
    highlighted: list[str] = []
    line_index = 0
    column_index = 0
    for char in current:
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
        if current_line.startswith("Updated:") or line_index >= len(previous_lines):
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
    load_totals: LoadTotals | None = None,
    load_summary: LoadSummary | None = None,
    allocation: dict | None = None,
) -> str:
    lines: list[str] = []
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    lines.append("Off-Grid Power Supervisor".ljust(width))
    lines.append(f"Updated: {format_updated_time(snapshot.captured_at)}")
    lines.append(_status_line(snapshot))
    lines.append("")

    lines.append("Load")
    if load_summary is not None:
        lines.append(_row("Now", f"{load_summary.current_a:.1f}A  {load_summary.power_w}W"))
        if load_summary.average_today_text is not None:
            lines.append(_row("3hr Rolling Avg", load_summary.average_today_text))
        if load_summary.today_text is not None:
            lines.append(_row("Cumulative Today", load_summary.today_text))
        if load_summary.remaining_text is not None:
            lines.append(_row("Estimated Autonomy", load_summary.remaining_text))
    elif load_totals is None:
        lines.append("  No data")
    else:
        lines.append(_row("Now", f"{load_totals.current_a:.1f}A  {load_totals.power_w:.0f}W"))
        lines.append(_row("Cumulative Today", f"{load_totals.consumed_ah:.1f}Ah {load_totals.consumed_percent:.1f}% of bank"))

    lines.append("")
    lines.extend(_battery_bank_lines(snapshot))

    lines.append("")
    lines.extend(_charge_controller_lines(snapshot))

    lines.append("")
    lines.extend(_inverter_charger_lines(snapshot))

    if allocation is not None:
        lines.append("")
        lines.extend(_allocation_lines(allocation))

    lines.append("")
    lines.extend(_temperature_lines(snapshot))

    if snapshot.errors:
        lines.append("")
        lines.append("Errors")
        for error in snapshot.errors:
            lines.append(f"  - {error}")
    if snapshot.status_conditions:
        lines.append("")
        lines.append("Status Conditions")
        for condition in snapshot.status_conditions:
            lines.append(f"  - {condition}")

    return "\n".join(lines)


def _status_line(snapshot: SupervisorSnapshot) -> str:
    status = f"Status:  {snapshot.status_text}"
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return f"SOC:  --  {status}"
    return f"SOC: {snapshot.battery.state_of_charge.soc_percent:3d}%  {status}"


def _battery_bank_lines(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["Battery Bank"]
    if snapshot.battery is None:
        lines.extend(_missing_battery_lines(snapshot))
        return lines

    battery = snapshot.battery
    measurements = battery.measurements
    limits = battery.charge_limits
    status = battery.status
    requests = battery.request_flags
    extended = battery.extended_measurements

    if measurements is not None:
        power_w = round(measurements.voltage_v * measurements.current_a)
        lines.append(_row("Flow", f"{measurements.voltage_v:.2f}V  {measurements.current_a:.1f}A  {power_w}W  {_battery_state(measurements.current_a)}"))
    if extended is not None and extended.min_cell_voltage_v is not None and extended.max_cell_voltage_v is not None:
        delta_mv = round((extended.max_cell_voltage_v - extended.min_cell_voltage_v) * 1000)
        min_location = format_cell_location_for_display(extended.min_cell_location_text())
        max_location = format_cell_location_for_display(extended.max_cell_location_text())
        value = f"Δ {delta_mv}mV"
        value += f"; min {min_location} {extended.min_cell_voltage_v:.3f}V"
        value += f"; max {max_location} {extended.max_cell_voltage_v:.3f}V"
        lines.append(_row("Cells", value))
    if status is not None:
        conditions = [*status.protection_flags, *status.alarm_flags]
        lines.append(_row("Protection/Alarms", "none" if not conditions else ", ".join(conditions)))
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
    if limits is not None:
        lines.append(_row("Limits", f"charge {limits.charge_voltage_limit_v:.1f}V/{limits.charge_current_limit_a:.1f}A  discharge {limits.discharge_current_limit_a:.1f}A"))
    return lines


def _missing_battery_lines(snapshot: SupervisorSnapshot) -> list[str]:
    if snapshot.battery_can_health is None:
        return ["  No CAN data"]
    if snapshot.battery_can_health.dfu_devices:
        lines = ["  CAN adapter: DFU/bootloader mode"]
        for device in snapshot.battery_can_health.dfu_devices[:2]:
            product = device.product or "STM32 DFU"
            serial = f" serial {device.serial}" if device.serial else ""
            lines.append(f"    - {product}{serial}")
        lines.append("  Action: replug USB-CAN adapter without BOOT/DFU pressed")
        return lines
    if not snapshot.battery_can_health.socketcan_present:
        return [f"  CAN adapter: interface {snapshot.battery_can_health.interface} not present"]
    return ["  No CAN frames received"]


def _charge_controller_lines(snapshot: SupervisorSnapshot) -> list[str]:
    lines: list[str] = []
    lines.append("Charge Controller 0 (Classic)")
    if snapshot.classic is None:
        lines.append("  No data")
    else:
        classic = snapshot.classic
        lines.append(_row("PV", f"{classic.pv_voltage_v:.1f}V  {classic.pv_current_a:.1f}A  Voc {classic.last_voc_v:.1f}V"))
        lines.append(_row("Output", f"{classic.battery_voltage_v:.1f}V  {classic.battery_current_a:.1f}A  {classic.battery_power_w}W"))
        lines.append(_row("Charge Status", classic.stage.render(classic.state)))
        if classic.is_hypervoc:
            lines.append(_row("PV input", f"HyperVOC protection  Last Voc {classic.last_voc_v:.1f}V  High {classic.highest_input_voltage_v:.1f}V"))
        lines.append(_row("Production Today", f"{classic.daily_energy_kwh:.1f}kWh  {classic.daily_amp_hours_ah}Ah"))
        if snapshot.classic_settings is not None:
            lines.append(_charge_settings_line(snapshot.classic_settings))

    lines.append("")
    lines.append("Charge Controller 1 (Epever)")
    if snapshot.epever is None:
        lines.append("  No data")
    else:
        epever = snapshot.epever
        lines.append(_row("PV", f"{epever.pv_voltage_v:.1f}V  {epever.pv_current_a:.1f}A  {epever.pv_power_w}W"))
        lines.append(_row("Output", f"{epever.battery_voltage_v:.1f}V  {epever.battery_current_a:.1f}A  {epever.battery_power_w}W"))
        lines.append(_row("Charge Status", epever.stage.render()))
        rated = f"{epever.rated_pv_voltage_v:.0f}V PV  {epever.rated_charging_current_a:.0f}A charge"
        lines.append(_row("Rated", rated))
        if snapshot.epever_settings is not None:
            lines.append(_epever_charge_settings_line(snapshot.epever_settings))
    return lines


def _charge_settings_line(settings: ClassicChargeSettings) -> str:
    return _row(
        "Charge Settings",
        f"Limit {settings.battery_current_limit_a:.1f}A  "
        f"Absorb {settings.absorb_voltage_v:.1f}V t={settings.absorb_time_s / 60:g}m  "
        f"Float {settings.float_voltage_v:.1f}V  "
        f"EQ {settings.equalize_voltage_v:.1f}V",
    )


def _epever_charge_settings_line(settings: EpeverChargeSettings) -> str:
    return _row(
        "Charge Settings",
        f"Limit {_current_text(settings.max_charging_current_a)}  "
        f"Absorb {settings.boost_voltage_v:.1f}V t={_minutes_text(settings.boost_time_minutes)}  "
        f"Float {settings.float_voltage_v:.1f}V  "
        f"EQ {settings.equalize_voltage_v:.1f}V",
    )


def _current_text(current_a: int | float | None) -> str:
    if current_a is None:
        return "--A"
    return f"{current_a:.1f}A"


def _minutes_text(minutes: int | float | None) -> str:
    if minutes is None:
        return "--"
    return f"{minutes:g}m"


def _inverter_charger_lines(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["Inverter/Charger"]
    inv = snapshot.magnum
    if inv is None:
        lines.append("  No data")
        return lines

    lines.append(_row("DC", f"{inv.dc_volts:.1f}V  {inv.dc_amps}A  {inv.dc_power_w}W"))

    ac_out_parts = [f"{inv.ac_volts_out}V"]
    if inv.ac_amps_out is not None:
        ac_out_parts.append(f"{inv.ac_amps_out}A")
    if inv.ac_freq_hz is not None:
        ac_out_parts.append(f"{inv.ac_freq_hz:.1f}Hz")
    lines.append(_row("AC Output", "  ".join(ac_out_parts)))

    if inv.ac_volts_in > 0:
        ac_in_parts = [f"{inv.ac_volts_in}V"]
        if inv.ac_amps_in is not None:
            ac_in_parts.append(f"{inv.ac_amps_in}A")
        lines.append(_row("AC Input", "  ".join(ac_in_parts)))
    else:
        lines.append(_row("AC Input", "0V  no source"))

    status_text = inv.status_label()
    fault = inv.fault_label()
    if fault:
        status_text += f"  Fault: {fault}"
    lines.append(_row("Status", status_text))

    settings_parts = []
    if inv.charger_amps_pct is not None and inv.charger_amps_pct > 0:
        settings_parts.append(f"Limit {inv.charger_amps_pct}%")
    if inv.absorb_v is not None:
        absorb = f"Absorb {inv.absorb_v:.1f}V"
        if inv.absorb_time_hr is not None:
            absorb += f" {inv.absorb_time_hr:.1f}h"
        settings_parts.append(absorb)
    if inv.float_v is not None:
        settings_parts.append(f"Float {inv.float_v:.1f}V")
    if inv.shore_amps is not None:
        settings_parts.append(f"Shore {inv.shore_amps}A")
    if settings_parts:
        lines.append(_row("Charge Settings", "  ".join(settings_parts)))

    return lines


def _allocation_lines(allocation: dict) -> list[str]:
    lines = ["Charge Allocation"]
    reason = allocation.get("reason") or "?"
    lines.append(_row("Limit", _allocation_limit_text(allocation)))
    basis = allocation.get("weight_basis")
    basis_text = f"  split by {basis}" if basis else ""
    lines.append(
        _row(
            "Budget",
            f"{_fmt_value(allocation.get('budget_a'), 0)}A  "
            f"(battery {_fmt_signed_a(allocation.get('battery_current_a', allocation.get('battery_charge_a')))}, "
            f"load {_fmt_value(allocation.get('load_allowance_a'), 0)}A){basis_text}",
        )
    )
    targets = allocation.get("targets") or {}
    for name in sorted(targets):
        target = targets[name]
        value = _allocation_target_text(target, global_reason=reason)
        if target.get("should_write"):
            value += "  *"
        lines.append(_row(name.capitalize(), value))
    return lines


def _allocation_limit_text(allocation: dict) -> str:
    mode = allocation.get("mode") or "?"
    reason = allocation.get("reason")
    allowance = allocation.get("allowance_a", allocation.get("charge_ceiling_a"))
    ccl = f"BMS CCL {_fmt_value(allocation.get('bms_ccl_a'), 0)}A"
    mechanism = _allocation_mechanisms_text(allocation)
    if reason == "unconstrained":
        return f"{mode}: not limiting ({ccl})"
    if allowance is None:
        return f"{mode}: no action ({mechanism}; {ccl})"
    if allowance <= 0:
        return f"{mode}: stop ({mechanism}; {ccl})"
    return f"{mode}: {_fmt_value(allowance, 0)}A net ({mechanism}; {ccl})"


def _allocation_mechanisms_text(allocation: dict) -> str:
    reason = allocation.get("reason")
    if reason == "unconstrained":
        return "none"
    if reason == "BMS CCL fraction":
        return "CCL taper"
    if reason == "feedback_clamp":
        return "CCL taper, feedback clamp"
    if reason == "BMS charge disabled":
        return "charge disabled"
    if reason == "BMS CCL is zero":
        return "BMS CCL zero"
    if reason == "full-charge latch":
        return "full-charge latch"
    if reason in {"cell safety latch", "charge_ceiling"}:
        return "cell safety stop"
    if isinstance(reason, str):
        if reason.startswith("max cell "):
            return "max-cell stop"
        if reason.startswith("cell delta "):
            return "cell-delta stop"
        if reason.startswith("missing "):
            return "no action: " + reason.removeprefix("missing ")
    return str(reason or "?")


def _allocation_target_text(target: dict, *, global_reason: str) -> str:
    reason = target.get("reason")
    target_a = target.get("target_a")
    if target.get("disable"):
        return _with_local_reason("off", reason, global_reason)
    if target_a is None:
        return _with_local_reason("--", reason, global_reason)
    value = f"{_fmt_value(target_a, 1)}A"
    if reason in {"charger inactive", "charger unavailable"}:
        return f"{value} released"
    if reason == "unconstrained":
        return f"{value} max"
    if reason in {"charger offline", "missing BMS CCL", "missing battery current"}:
        return _with_local_reason(value, reason, global_reason)
    return f"{value} limited"


def _with_local_reason(value: str, reason: str | None, global_reason: str) -> str:
    if reason and reason != global_reason:
        return f"{value} ({reason})"
    return value


def _fmt_value(value, digits: int = 1) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.{digits}f}" if digits else f"{number:.0f}"


def _fmt_signed_a(value) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{value}A"
    return f"{number:+.0f}A"


def _temperature_lines(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["Temperatures"]
    if (
        snapshot.battery is not None
        and snapshot.battery.extended_measurements is not None
        and snapshot.battery.extended_measurements.min_cell_temperature_c is not None
        and snapshot.battery.extended_measurements.max_cell_temperature_c is not None
    ):
        extended = snapshot.battery.extended_measurements
        lines.append(_row("Battery cells", f"{extended.min_cell_temperature_c:.1f}-{extended.max_cell_temperature_c:.1f}C"))
    if snapshot.classic is not None:
        classic = snapshot.classic
        lines.append(_row("Battery terminal", f"{classic.battery_temp_c:.1f}C"))
        lines.append(_row("CC0 FET", f"{classic.fet_temp_c:.1f}C"))
        lines.append(_row("CC0 PCB", f"{classic.pcb_temp_c:.1f}C"))
    if snapshot.magnum is not None:
        inv = snapshot.magnum
        lines.append(_row("INV battery", f"{inv.battery_temp_c}C"))
        lines.append(_row("INV transformer", f"{inv.transformer_temp_c}C"))
        lines.append(_row("INV FET", f"{inv.fet_temp_c}C"))
    if snapshot.ambient is None:
        lines.append(_row("Sensor 0 ambient temp", "disconnected"))
    else:
        ambient = snapshot.ambient
        lines.append(_row("Sensor 0 ambient temp", f"{ambient.temperature_c:.1f}C"))
        if ambient.humidity_percent is not None:
            lines.append(_row("Humidity", f"{ambient.humidity_percent:.1f}%"))
    return lines


def _row(label: str, value: str) -> str:
    return f"  {label + ':':<{ROW_LABEL_WIDTH + 1}} {value}"


def _battery_state(current_a: float) -> str:
    if current_a > BATTERY_IDLE_CURRENT_A:
        return "charging"
    if current_a < -BATTERY_IDLE_CURRENT_A:
        return "discharging"
    return "idle"
