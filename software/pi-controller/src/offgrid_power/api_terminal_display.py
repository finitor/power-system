"""Terminal rendering for supervisor API snapshots."""

from __future__ import annotations

from datetime import datetime
import shutil

from .charge_stage import NormalizedStage
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
    lines.extend(_inverter_charger_lines(payload.get("inverter")))
    magnum_err = (payload.get("reader_error_rates") or {}).get("magnum")
    if magnum_err is not None:
        lines.append(_row("RS485 Glitches", f"{magnum_err:.1f}% (5 min)"))

    if payload.get("allocation") is not None:
        lines.append("")
        lines.extend(_allocation_lines(payload["allocation"], solar=payload.get("solar") or []))

    lines.append("")
    lines.extend(_temperature_lines(payload))

    # One off-normal group: read failures and analyzed conditions/BMS faults all
    # mean "something is wrong", and merging saves vertical space.
    off_normal = [*(status.get("errors") or []), *(status.get("conditions") or [])]
    if off_normal:
        lines.append("")
        lines.append("Warnings and Faults")
        for item in off_normal:
            lines.append(f"  - {item}")

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
    """Render the normalized weather API payload (see weather.weather_api_payload)."""
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    label = payload.get("label") or "Weather"
    observed_at = _parse_datetime(payload.get("observed_at"))

    lines: list[str] = [f"Off-Grid Weather - {label}".ljust(width)]
    if observed_at is None:
        lines.append("As of: unavailable")
    else:
        lines.append(f"As of: {format_updated_time(observed_at)}")
    if payload.get("stale"):
        lines.append("Using last cached weather; WAN fetch failed.")
    if payload.get("error"):
        lines.append(f"Note: {payload['error']}")

    current = payload.get("current")
    if not current:
        lines.append("")
        lines.append("Weather unavailable")
        return "\n".join(lines)

    wind = current.get("wind") or {}
    irradiance = current.get("irradiance") or {}
    lines.append("")
    lines.append("Current")
    lines.append(_row("Condition", (current.get("condition") or {}).get("text") or "--"))
    lines.append(_row("Temperature", _measure(current.get("temperature_c"), "C", 1) or "--"))
    for row_label, value in [
        ("Feels Like", _measure(current.get("apparent_temperature_c"), "C", 1)),
        ("Humidity", _measure(current.get("humidity_pct"), "%", 0)),
        ("Cloud", _measure(current.get("cloud_cover_pct"), "%", 0)),
        ("Wind", _wind_text(wind)),
        ("Precip Now", _precip_text(current)),
    ]:
        if value is not None:
            lines.append(_row(row_label, value))

    hourly = payload.get("hourly") or []
    if hourly:
        lines.append("")
        lines.append("Next Hours")
        for hour in hourly[:8]:
            lines.append(
                _row(
                    _clock(hour.get("at")),
                    _join(
                        (hour.get("condition") or {}).get("text"),
                        _measure(hour.get("temperature_c"), "C", 1),
                        _measure(hour.get("precip_probability_pct"), "% precip", 0),
                        _measure(hour.get("wind_speed_kmh"), "km/h", 0),
                    ),
                )
            )

    daily = payload.get("daily") or []
    if daily:
        lines.append("")
        lines.append("Forecast")
        for day in daily[:3]:
            lines.append(
                _row(
                    _day(day.get("date")),
                    _join(
                        (day.get("condition") or {}).get("text"),
                        _temp_range(day.get("low_c"), day.get("high_c")),
                        _measure(day.get("precip_probability_pct"), "% precip", 0),
                        _measure(day.get("precip_sum_mm"), "mm", 1),
                    ),
                )
            )

    lines.append("")
    lines.append("Solar Irradiance")
    lines.append(_row("Global Horizontal", _measure(irradiance.get("ghi_wm2"), "W/m2", 0) or "--"))
    lines.append(_row("Direct Radiation", _measure(irradiance.get("direct_wm2"), "W/m2", 0) or "--"))
    lines.append(_row("Diffuse Radiation", _measure(irradiance.get("diffuse_wm2"), "W/m2", 0) or "--"))
    lines.append(_row("Direct Normal", _measure(irradiance.get("dni_wm2"), "W/m2", 0) or "--"))

    astronomy = payload.get("astronomy") or {}
    lines.append("")
    lines.append("Astronomy")
    sun = _sun_text(astronomy.get("sunrise"), astronomy.get("sunset"))
    if sun:
        lines.append(_row("Sun", sun))
    moon = astronomy.get("moon") or {}
    if moon.get("name"):
        phase = moon.get("phase")
        suffix = f" ({phase:.2f})" if isinstance(phase, (int, float)) else ""
        lines.append(_row("Moon", f"{moon['name']}{suffix}"))
    lines.extend(_aurora_lines(astronomy.get("aurora")))

    return "\n".join(lines)


def _aurora_lines(aurora: object) -> list[str]:
    if not isinstance(aurora, dict):
        return []
    lines: list[str] = []
    probability = _measure(aurora.get("probability_pct"), "%", 0)
    if probability:
        text = f"now {probability}"
        valid_at = aurora.get("valid_at")
        if isinstance(valid_at, str):
            text = f"{text} valid {_clock(valid_at)}"
        lines.append(_row("Aurora", text))
    tonight = aurora.get("tonight")
    if isinstance(tonight, dict):
        kp = _measure(tonight.get("peak_kp"), "", 1)
        likelihood = tonight.get("likelihood")
        if kp is not None and isinstance(likelihood, str):
            time_text = _clock(tonight.get("peak_at")) if isinstance(tonight.get("peak_at"), str) else "--"
            scale = tonight.get("scale")
            scale_text = f" {scale}" if scale else ""
            lines.append(_row("Aurora Tonight", f"{likelihood}  peak Kp {kp}{scale_text} around {time_text}"))
    return lines


def _measure(value: object, suffix: str = "", decimals: int = 1) -> str | None:
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return None


def _join(*items: str | None) -> str:
    return "  ".join(item for item in items if item)


def _wind_text(wind: dict) -> str | None:
    speed = _measure(wind.get("speed_kmh"), "km/h", 0)
    if speed is None:
        return None
    return _join(speed, _measure(wind.get("gust_kmh"), "km/h gust", 0), wind.get("compass"))


def _precip_text(current: dict) -> str | None:
    return (
        _join(
            _measure(current.get("precipitation_mm"), "mm", 1),
            _measure(current.get("rain_mm"), "mm rain", 1),
            _measure(current.get("snowfall_cm"), "cm snow", 1),
        )
        or None
    )


def _temp_range(low: object, high: object) -> str | None:
    low_text = _measure(low, "C", 1)
    high_text = _measure(high, "C", 1)
    if low_text and high_text:
        return f"{low_text}-{high_text}"
    return high_text or low_text


def _sun_text(sunrise: object, sunset: object) -> str | None:
    rise = _clock(sunrise) if isinstance(sunrise, str) else "--"
    set_ = _clock(sunset) if isinstance(sunset, str) else "--"
    if rise == "--" and set_ == "--":
        return None
    return f"rise {rise}  set {set_}"


def _clock(value: object) -> str:
    parsed = _parse_datetime(value) if isinstance(value, str) else None
    if parsed is None:
        return value if isinstance(value, str) else "--"
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%H:%M")


def _day(value: object) -> str:
    parsed = _parse_datetime(value) if isinstance(value, str) else None
    if parsed is None:
        return value if isinstance(value, str) else "--"
    return parsed.strftime("%a %m/%d")
    return lines


def _status_line(status: dict, battery: dict) -> str:
    severity = status.get("severity") or status.get("status") or "UNKNOWN"
    annotations = status.get("annotations") or []
    if annotations:
        severity += " (" + ", ".join(annotations) + ")"
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

    # Protections/alarms surface as Status Conditions (off-normal status), not a
    # passive battery row.
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
        return ["Charge Controllers", "  No data"]
    for index, controller in enumerate(solar):
        if lines:
            lines.append("")
        title = _charge_controller_title(index, controller)
        if controller.get("status") == "unreachable":
            lines.append(f"{title} — UNREACHABLE")
            continue
        lines.append(title)
        for condition in controller.get("conditions") or []:
            lines.append(_row("Alert", condition))
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
            parts = []
            if controller.get("daily_energy_kwh") is not None:
                parts.append(_energy_text(controller.get("daily_energy_kwh")))
            if controller.get("daily_amp_hours_ah") is not None:
                parts.append(f"{_fmt(controller.get('daily_amp_hours_ah'), 0)}Ah")
            lines.append(_row("Production Today", "  ".join(parts)))
        elif controller.get("daily_energy_unavailable_reason"):
            lines.append(_row("Production Today", str(controller.get("daily_energy_unavailable_reason"))))
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
                    f"{_fmt(settings.get('current_limit_a'), 1)}A "
                    f"Abs {_fmt(settings.get('absorb_voltage_v'), 1)}V/{_minutes_text(settings.get('absorb_time_minutes'))} "
                    f"Flt {_fmt(settings.get('float_voltage_v'), 1)}V "
                    f"TCV {_fmt(settings.get('max_temp_comp_voltage_v'), 1)}V"
                )
            else:
                value = (
                    f"{_measure(settings.get('max_charging_current_a'), 'A', 1) or '--A'} "
                    f"Abs {_fmt(_first_present(settings, 'absorb_voltage_v', 'boost_voltage_v'), 1)}V/{_minutes_text(settings.get('absorb_time_minutes'))} "
                    f"Flt {_fmt(settings.get('float_voltage_v'), 1)}V "
                    f"Rec {_fmt(_first_present(settings, 'bulk_recovery_voltage_v', 'boost_reconnect_voltage_v'), 1)}V"
                )
            lines.append(_row("Charge Settings", value))
    return lines


def _charge_controller_title(index: int, controller: dict) -> str:
    name = _charge_controller_short_name(controller)
    return f"Charge Controller {index} ({name})" if name else f"Charge Controller {index}"


def _charge_controller_short_name(controller: dict) -> str:
    device = controller.get("device") or {}
    short_name = str(device.get("short_name") or "").strip()
    if short_name:
        return short_name
    return " ".join(str(part).strip() for part in [device.get("vendor"), device.get("model")] if part)


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
        lines.append(_row("Charge Settings", " ".join(settings_parts)))

    return lines


def _allocation_lines(allocation: dict, solar: list[dict] | None = None) -> list[str]:
    header = "Charge Allocation"
    if allocation.get("allocator_paused"):
        header += "  (paused)"
    lines = [header]
    reason = allocation.get("reason") or "?"
    lines.append(_row("Limit", _allocation_limit_text(allocation)))
    lines.append(_row("Budget", _allocation_budget_text(allocation)))
    targets = allocation.get("targets") or {}
    labels = _allocation_target_labels(solar or [])
    for name in sorted(targets):
        target = targets[name]
        value = _allocation_target_text(target, global_reason=reason)
        if target.get("should_write"):
            value += "  *"  # a write is pending/applied this cycle
        lines.append(_row(labels.get(name, name.capitalize()), value))
    return lines


def _allocation_target_labels(solar: list[dict]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for controller in solar:
        controller_id = controller.get("id")
        if not isinstance(controller_id, str):
            continue
        key = controller_id.split(".", 1)[0]
        label = _charge_controller_short_name(controller)
        if label:
            labels[key] = label
    return labels


def _allocation_budget_text(allocation: dict) -> str:
    basis_text = _allocation_basis_text(allocation.get("weight_basis"))
    budget = f"{_fmt(allocation.get('budget_a'), 0)}A"
    reason = allocation.get("reason")
    ceiling = allocation.get("allowance_a", allocation.get("charge_ceiling_a"))
    if reason == "feedback_clamp":
        return (
            f"{budget}  feedback: battery "
            f"{_fmt_signed_a(allocation.get('battery_current_a', allocation.get('battery_charge_a')))} "
            f"> ceiling {_fmt(ceiling, 0)}A{basis_text}"
        )
    if reason == "BMS CCL fraction":
        return f"{budget}  includes load {_fmt(allocation.get('load_allowance_a'), 0)}A{basis_text}"
    return f"{budget}{basis_text}"


def _allocation_basis_text(basis: str | None) -> str:
    if basis == "equal":
        return "  split equally"
    return f"  split by {basis}" if basis else ""


def _allocation_limit_text(allocation: dict) -> str:
    mode = allocation.get("mode") or "?"
    reason = allocation.get("reason")
    allowance = allocation.get("allowance_a", allocation.get("charge_ceiling_a"))
    ccl = f"BMS CCL {_fmt(allocation.get('bms_ccl_a'), 0)}A"
    budget = _ccl_scaling_factor_text(allocation)
    mechanism = _allocation_mechanisms_text(allocation)
    prefix = "dry-run: " if mode == "dry-run" else ""
    if reason == "unconstrained":
        return f"{prefix}not limiting ({ccl}{budget})"
    if allowance is None:
        return f"{prefix}no action ({mechanism}; {ccl})"
    if allowance <= 0:
        return f"{prefix}stop ({mechanism}; {ccl})"
    return f"{prefix}{_fmt(allowance, 0)}A net ({mechanism}; {ccl}{budget})"


def _ccl_scaling_factor_text(allocation: dict) -> str:
    """The live CCL scaling factor as a '; budget NN%' segment, or '' if absent."""
    fraction = allocation.get("ccl_scaling_factor")
    return f"; scaling {fraction * 100:.0f}%" if isinstance(fraction, (int, float)) else ""


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
    if reason == "low temperature latch":
        return "low-temperature stop"
    if isinstance(reason, str):
        if reason.startswith("battery temp "):
            return "low-temperature stop"
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
    ceiling = target.get("manual_ceiling_a")
    if target.get("disable"):
        return _with_local_reason("off", reason, global_reason)
    if target_a is None:
        return _with_local_reason("--", reason, global_reason)
    value = f"{_fmt(target_a, 1)}A"
    if reason in {"unconstrained", "charger inactive", "charger unavailable"}:
        base = f"{value} released"
    elif reason in {"charger offline", "missing BMS CCL", "missing battery current"}:
        base = _with_local_reason(value, reason, global_reason)
    else:
        base = f"{value} limited"
    if ceiling is not None:
        return f"{base} → {_fmt(ceiling, 0)}A manual ceiling"
    return base


def _with_local_reason(value: str, reason: str | None, global_reason: str) -> str:
    if reason and reason != global_reason:
        return f"{value} ({reason})"
    return value


def _temperature_lines(payload: dict) -> list[str]:
    lines = ["Temperatures"]
    battery = payload.get("battery") or {}
    ambient = payload.get("ambient") or {}
    solar = payload.get("solar") or []
    inverter = payload.get("inverter") or {}
    cell_min = battery.get("cell_temperature_min_c")
    cell_max = battery.get("cell_temperature_max_c")
    if cell_min is not None and cell_max is not None:
        cell_temp_str = f"{_fmt(cell_min, 0)}-{_fmt(cell_max, 0)}C"
        if (payload.get("relay") or {}).get("heat_fan"):
            cell_temp_str += "  HEATING"
        lines.append(_row("Battery cells", cell_temp_str))
    # Suppressed temperature rows (hidden from the display by request 2026-06-17;
    # the data is still in the snapshot, so restore by re-adding these:)
    #   - "Battery terminal"  <- solar[0].temperatures_c["battery"]  (CC0 battery sensor)
    #   - "INV battery"        <- inverter["battery_temp_c"]
    for index, controller in enumerate(solar):
        temps = controller.get("temperatures_c") or {}
        prefix = f"CC{index}"
        if temps.get("fet") is not None:
            lines.append(_row(f"{prefix} FET", f"{_fmt(temps.get('fet'), 1)}C"))
        if temps.get("pcb") is not None:
            lines.append(_row(f"{prefix} PCB", f"{_fmt(temps.get('pcb'), 1)}C"))
    if inverter.get("transformer_temp_c") is not None:
        lines.append(_row("INV transformer", f"{_fmt(inverter.get('transformer_temp_c'), 0)}C"))
    if inverter.get("fet_temp_c") is not None:
        lines.append(_row("INV FET", f"{_fmt(inverter.get('fet_temp_c'), 0)}C"))
    if ambient.get("temperature_c") is None:
        lines.append(_row("Sensor 0 ambient", "disconnected"))
    else:
        lines.append(_row("Sensor 0 ambient", f"{_fmt(ambient.get('temperature_c'), 1)}C"))
        if ambient.get("humidity_percent") is not None:
            lines.append(_row("Humidity", f"{_fmt(ambient.get('humidity_percent'), 1)}%"))
    return lines


def _minutes_text(minutes) -> str:
    try:
        return f"{float(minutes):g}m"
    except (TypeError, ValueError):
        return "--"


def _first_present(mapping: dict, *keys: str):
    for key in keys:
        if mapping.get(key) is not None:
            return mapping.get(key)
    return None


def _row(label: str, value: str) -> str:
    return f"  {label:<{ROW_LABEL_WIDTH}} {value}"


def _fmt(value, decimals: int) -> str:
    if value is None:
        return "?"
    return f"{float(value):.{decimals}f}"


def _fmt_signed_a(value) -> str:
    if value is None:
        return "?"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{value}A"
    return f"{number:+.0f}A"


def _energy_text(kwh) -> str:
    """Readable energy: Wh (integer) below 1 kWh, kWh (1 decimal) above. The
    underlying counters are 10 Wh (EPEver) / 100 Wh (Classic) resolution, so the
    Wh value is quantized to those steps -- this just avoids the '0.0x kWh' clutter."""
    if kwh is None:
        return "?"
    wh = float(kwh) * 1000
    return f"{round(wh)}Wh" if abs(wh) < 1000 else f"{float(kwh):.1f}kWh"


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
