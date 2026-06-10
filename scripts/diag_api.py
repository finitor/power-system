#!/usr/bin/env python3
"""Summarize a supervisor /api/v1/snapshot JSON document from stdin.

Used by diag.sh; one line per subsystem, never raises.
"""

import json
import sys


def main() -> int:
    try:
        p = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        print(f"api: no/bad response ({exc})")
        return 0
    status = p.get("status")
    if not isinstance(status, dict):
        print(f"api: starting up ({p.get('error') or status})")
        return 0
    print(f"api: ok={status.get('ok')} severity={status.get('severity')} age={p.get('age_seconds')}s")
    print(f"errors: {status.get('errors') or 'none'}")
    print(f"conditions: {status.get('conditions') or 'none'}")
    battery = p.get("battery") or {}
    print(f"battery: soc={battery.get('soc_percent')} v={battery.get('voltage_v')} a={battery.get('current_a')}")
    solar = (p.get("solar") or [{}])[0]
    print(f"classic: w={solar.get('battery_power_w')} stage={solar.get('charge_stage')}")
    inverter = p.get("inverter") or {}
    print(f"magnum: dc={inverter.get('dc_volts')}V status={inverter.get('status_label')}")
    ambient = p.get("ambient") or {}
    print(f"ambient: {ambient.get('temperature_c')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
