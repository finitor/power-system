"""Terminal rendering for supervisor API snapshots."""

from __future__ import annotations

from datetime import datetime
import shutil

from .charge_stage import NormalizedStage
from .terminal_display import format_cell_location_for_display, format_updated_time
from .weather import weather_code_text
from .web_display import (
    _daily_temperature_text,
    _format_number,
    _indexed,
    _moon_phase_text,
    _precip_text,
    _short_day,
    _short_time,
    _sun_text,
    _wind_text,
)


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
    lines.extend(_inverter_charger_lines(payload.get("inverter")))

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


def render_api_weather(payload: dict, now: datetime | None = None) -> str:
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    label = payload.get("label") or "Weather"
    data = payload.get("data") or {}
    fetched_at = _parse_datetime(payload.get("fetched_at"))

    lines: list[str] = [f"Off-Grid Weather - {label}".ljust(width)]
    if fetched_at is None:
        lines.append("As of: unavailable")
    else:
        lines.append(f"As of: {format_updated_time(fetched_at)}")
    if payload.get("stale"):
        lines.append("Using last cached weather; WAN fetch failed.")
    if payload.get("error"):
        lines.append(f"Note: {payload['error']}")

    if not data:
        lines.append("")
        lines.append("Weather unavailable")
        return "\n".join(lines)

    current = data.get("current") or {}
    lines.append("")
    lines.append("Current")
    lines.append(_row("Condition", weather_code_text(current.get("weather_code"))))
    lines.append(_row("Temperature", _format_number(current.get("temperature_2m"), "C", decimals=1) or "--"))
    for row_label, value in [
        ("Feels Like", _format_number(current.get("apparent_temperature"), "C", decimals=1)),
        ("Humidity", _format_number(current.get("relative_humidity_2m"), "%", decimals=0)),
        ("Cloud", _format_number(current.get("cloud_cover"), "%", decimals=0)),
        (
            "Wind",
            _wind_text(
                current.get("wind_speed_10m"),
                current.get("wind_gusts_10m"),
                current.get("wind_direction_10m"),
            ),
        ),
        ("Precip Now", _precip_text(current.get("precipitation"), current.get("rain"), current.get("snowfall"))),
    ]:
        if value is not None:
            lines.append(_row(row_label, value))

    hourly = data.get("hourly") or {}
    hours = hourly.get("time") or []
    if hours:
        lines.append("")
        lines.append("Next Hours")
        for index, hour in enumerate(hours[:8]):
            lines.append(
                _row(
                    _short_time(hour),
                    "  ".join(
                        item
                        for item in [
                            weather_code_text(_indexed(hourly.get("weather_code"), index)),
                            _format_number(_indexed(hourly.get("temperature_2m"), index), "C", decimals=1),
                            _format_number(_indexed(hourly.get("precipitation_probability"), index), "% precip", decimals=0),
                            _format_number(_indexed(hourly.get("wind_speed_10m"), index), "km/h", decimals=0),
                        ]
                        if item
                    ),
                )
            )

    daily = data.get("daily") or {}
    days = daily.get("time") or []
    if days:
        lines.append("")
        lines.append("Forecast")
        for index, day in enumerate(days[:3]):
            lines.append(
                _row(
                    _short_day(day),
                    "  ".join(
                        item
                        for item in [
                            weather_code_text(_indexed(daily.get("weather_code"), index)),
                            _daily_temperature_text(
                                _indexed(daily.get("temperature_2m_min"), index),
                                _indexed(daily.get("temperature_2m_max"), index),
                            ),
                            _format_number(_indexed(daily.get("precipitation_probability_max"), index), "% precip", decimals=0),
                            _format_number(_indexed(daily.get("precipitation_sum"), index), "mm", decimals=1),
                        ]
                        if item
                    ),
                )
            )

    lines.append("")
    lines.append("Solar Irradiance")
    lines.append(_row("Global Horizontal", _format_number(current.get("shortwave_radiation"), "W/m2", decimals=0) or "--"))
    lines.append(_row("Direct Radiation", _format_number(current.get("direct_radiation"), "W/m2", decimals=0) or "--"))
    lines.append(_row("Diffuse Radiation", _format_number(current.get("diffuse_radiation"), "W/m2", decimals=0) or "--"))
    lines.append(_row("Direct Normal", _format_number(current.get("direct_normal_irradiance"), "W/m2", decimals=0) or "--"))

    lines.append("")
    lines.append("Astronomy")
    sun = _sun_text(_indexed(daily.get("sunrise"), 0), _indexed(daily.get("sunset"), 0))
    if sun:
        lines.append(_row("Sun", sun))
    moon = _moon_phase_text(_indexed(daily.get("moon_phase"), 0))
    if moon:
        lines.append(_row("Moon", moon))
    lines.extend(_aurora_lines(data.get("aurora")))

    return "\n".join(lines)


def _aurora_lines(aurora: object) -> list[str]:
    if not isinstance(aurora, dict) or aurora.get("error"):
        return []
    lines: list[str] = []
    probability = _format_number(aurora.get("probability_percent"), "%", decimals=0)
    if probability:
        text = f"now {probability}"
        forecast_time = aurora.get("forecast_time")
        if isinstance(forecast_time, str):
            text = f"{text} valid {_short_time(forecast_time)}"
        lines.append(_row("Aurora", text))
    tonight = aurora.get("tonight")
    if isinstance(tonight, dict) and not tonight.get("error"):
        kp = _format_number(tonight.get("peak_kp"), "", decimals=1)
        likelihood = tonight.get("likelihood")
        if kp is not None and isinstance(likelihood, str):
            peak_time = tonight.get("peak_time")
            time_text = _short_time(peak_time) if isinstance(peak_time, str) else "--"
            scale = tonight.get("noaa_scale")
            scale_text = f" {scale}" if scale else ""
            lines.append(_row("Aurora Tonight", f"{likelihood}  peak Kp {kp}{scale_text} around {time_text}"))
    return lines


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
        stage = NormalizedStage.from_dict(controller.get("charge_stage"))
        lines.append(_row("Charge Status", stage.render(controller.get("state"))))
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


def _inverter_charger_lines(inverter: dict | None) -> list[str]:
    lines = ["Inverter/Charger"]
    if inverter is None:
        lines.append("  No data")
        return lines

    lines.append(
        _row(
            "DC",
            f"{_fmt(inverter.get('dc_volts'), 1)}V  "
            f"{_fmt(inverter.get('dc_amps'), 0)}A  "
            f"{_fmt(inverter.get('dc_power_w'), 0)}W",
        )
    )

    ac_out_parts = [f"{_fmt(inverter.get('ac_volts_out'), 0)}V"]
    if inverter.get("ac_amps_out") is not None:
        ac_out_parts.append(f"{_fmt(inverter.get('ac_amps_out'), 0)}A")
    if inverter.get("ac_freq_hz") is not None:
        ac_out_parts.append(f"{_fmt(inverter.get('ac_freq_hz'), 1)}Hz")
    lines.append(_row("AC Output", "  ".join(ac_out_parts)))

    ac_volts_in = inverter.get("ac_volts_in") or 0
    if ac_volts_in > 0:
        ac_in_parts = [f"{_fmt(ac_volts_in, 0)}V"]
        if inverter.get("ac_amps_in") is not None:
            ac_in_parts.append(f"{_fmt(inverter.get('ac_amps_in'), 0)}A")
        lines.append(_row("AC Input", "  ".join(ac_in_parts)))
    else:
        lines.append(_row("AC Input", "0V  no source"))

    status_label = inverter.get("status_label") or inverter.get("status") or "unknown"
    fault = inverter.get("fault")
    if fault and fault not in ("NONE", "UNKNOWN"):
        status_label += f"  Fault: {fault.lower().replace('_', ' ')}"
    lines.append(_row("Status", status_label))

    settings = inverter.get("settings") or {}
    settings_parts = []
    if settings.get("charger_amps_pct") is not None and (settings.get("charger_amps_pct") or 0) > 0:
        settings_parts.append(f"Limit {_fmt(settings.get('charger_amps_pct'), 0)}%")
    if settings.get("absorb_v") is not None:
        absorb = f"Absorb {_fmt(settings.get('absorb_v'), 1)}V"
        if settings.get("absorb_time_hr") is not None:
            absorb += f" {_fmt(settings.get('absorb_time_hr'), 1)}h"
        settings_parts.append(absorb)
    if settings.get("float_v") is not None:
        settings_parts.append(f"Float {_fmt(settings.get('float_v'), 1)}V")
    if settings.get("shore_amps") is not None:
        settings_parts.append(f"Shore {_fmt(settings.get('shore_amps'), 0)}A")
    if settings_parts:
        lines.append(_row("Charge Settings", "  ".join(settings_parts)))

    return lines


def _temperature_lines(payload: dict) -> list[str]:
    lines = ["Temperatures"]
    battery = payload.get("battery") or {}
    ambient = payload.get("ambient") or {}
    solar = payload.get("solar") or []
    inverter = payload.get("inverter") or {}
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
    if inverter.get("battery_temp_c") is not None:
        lines.append(_row("INV battery", f"{_fmt(inverter.get('battery_temp_c'), 0)}C"))
    if inverter.get("transformer_temp_c") is not None:
        lines.append(_row("INV transformer", f"{_fmt(inverter.get('transformer_temp_c'), 0)}C"))
    if inverter.get("fet_temp_c") is not None:
        lines.append(_row("INV FET", f"{_fmt(inverter.get('fet_temp_c'), 0)}C"))
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
