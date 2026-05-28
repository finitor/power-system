"""Terminal display rendering for the Pi supervisor."""

from __future__ import annotations

import os
import shutil
from datetime import datetime

from .supervisor import SupervisorSnapshot


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def format_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def render_snapshot(snapshot: SupervisorSnapshot) -> str:
    lines: list[str] = []
    width = min(shutil.get_terminal_size((100, 30)).columns, 120)
    lines.append("Off-Grid Power Supervisor".ljust(width))
    lines.append(f"Updated: {format_time(snapshot.captured_at)}")
    lines.append(f"Status:  {'OK' if snapshot.ok else 'ERROR'}")
    lines.append("")

    if snapshot.classic is None:
        lines.append("MidNite Classic")
        lines.append("  No data")
    else:
        classic = snapshot.classic
        lines.append("MidNite Classic")
        lines.append(f"  Battery: {classic.battery_voltage_v:5.1f} V  {classic.battery_current_a:5.1f} A  {classic.battery_power_w:5d} W")
        lines.append(f"  PV:      {classic.pv_voltage_v:5.1f} V  {classic.pv_current_a:5.1f} A")
        lines.append(f"  Stage:   {classic.charge_stage}  State: {classic.state}")
        lines.append(f"  Today:   {classic.daily_energy_kwh:5.1f} kWh  {classic.daily_amp_hours_ah:5d} Ah")
        lines.append(f"  Temps:   batt {classic.battery_temp_c:4.1f} C  FET {classic.fet_temp_c:4.1f} C  PCB {classic.pcb_temp_c:4.1f} C")
        if classic.active_flags:
            lines.append("  Flags:")
            for flag in classic.active_flags[:6]:
                lines.append(f"    - {flag}")
            if len(classic.active_flags) > 6:
                lines.append(f"    - ... {len(classic.active_flags) - 6} more")
        else:
            lines.append("  Flags:   none decoded")

    lines.append("")
    if snapshot.classic_settings is not None:
        settings = snapshot.classic_settings
        lines.append("Classic Charge Settings")
        lines.append(f"  Limit:   {settings.battery_current_limit_a:5.1f} A")
        lines.append(f"  Absorb:  {settings.absorb_voltage_v:5.1f} V for {settings.absorb_time_s} s")
        lines.append(f"  Float:   {settings.float_voltage_v:5.1f} V")
        lines.append(f"  EQ:      {settings.equalize_voltage_v:5.1f} V")

    if snapshot.errors:
        lines.append("")
        lines.append("Errors")
        for error in snapshot.errors:
            lines.append(f"  - {error}")

    lines.append("")
    lines.append("Press Ctrl-C to exit. Read-only monitor; no control writes are performed.")
    return "\n".join(lines)
