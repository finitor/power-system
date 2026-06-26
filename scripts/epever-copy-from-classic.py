#!/usr/bin/env python3
"""One-shot copy of charge parameters from the MidNite Classic to the EPEver.

The Classic is treated as the baseline. This aligns the EPEver's charge
voltages (and optionally the charge-current limit) to the Classic's, so both
controllers pursue the same staged-charging goal. It maps:

    Classic absorb_voltage   -> EPEver boost (absorption) voltage  (0x900B)
    Classic float_voltage    -> EPEver float voltage               (0x900C)
    Classic equalize_voltage -> EPEver equalize voltage            (0x900A)
    Classic current limit    -> EPEver max charging current        (0x9013)

An optional voltage offset can be added to the Classic voltage targets before
writing the EPEver. This is useful when the EPEver tapers early relative to the
Classic and needs a slightly higher local setpoint to keep contributing.

The EPEver's protection thresholds (over-voltage disconnect/reconnect,
low-voltage disconnect/reconnect, discharge limit) have no Classic
counterpart and are left untouched.

Dry-run by default: prints the planned diff and writes nothing. Pass --write
to apply through the supervisor API. Charge-voltage writes require EPEver
Battery Type = User; the supervisor-side writer enforces this and aborts
otherwise.

    .venv/bin/python scripts/epever-copy-from-classic.py            # dry-run
    .venv/bin/python scripts/epever-copy-from-classic.py --voltage-offset 0.30
    .venv/bin/python scripts/epever-copy-from-classic.py --write    # apply
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.classic import ClassicClient
from offgrid_power.config import load_config
from offgrid_power.epever import EpeverClient

DEFAULT_API_URL = "http://127.0.0.1:8081"


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--classic-host", default=config.classic.host)
    parser.add_argument("--classic-port", type=int, default=config.classic.port)
    parser.add_argument("--classic-device-id", type=int, default=config.classic.device_id)
    parser.add_argument("--classic-timeout", type=float, default=config.classic.timeout_s)
    parser.add_argument("--epever-device", default=config.epever.device)
    parser.add_argument("--epever-baud", type=int, default=config.epever.baud)
    parser.add_argument("--epever-unit", type=int, default=config.epever.unit)
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="supervisor base URL")
    parser.add_argument("--direct-epever", action="store_true", help="open the EPEver serial device directly")
    parser.add_argument(
        "--voltage-offset",
        type=float,
        default=0.0,
        help="volts to add to Classic voltage targets before writing EPEver",
    )
    parser.add_argument(
        "--no-current",
        action="store_true",
        help="Copy only the charge voltages; leave the EPEver current limit alone",
    )
    parser.add_argument("--write", action="store_true", help="Apply the changes (default is dry-run)")
    return parser.parse_args()


def _fmt(value: float) -> str:
    return f"{value:.2f}V"


def api_json(base_url: str, path: str, payload: dict | None = None) -> dict:
    url = base_url.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach supervisor API at {url}: {exc}") from exc


def epever_settings_from_api(base_url: str) -> dict:
    snapshot = api_json(base_url, "/api/v1/snapshot")
    for controller in snapshot.get("solar") or []:
        if controller.get("id") == "epever.1":
            settings = controller.get("settings")
            if settings is None:
                raise RuntimeError("supervisor snapshot has EPEver telemetry but no settings")
            return settings
    raise RuntimeError("supervisor snapshot does not include epever.1")


def main() -> int:
    args = parse_args()

    classic_client = ClassicClient(
        host=args.classic_host,
        port=args.classic_port,
        device_id=args.classic_device_id,
        timeout=args.classic_timeout,
    )
    _, classic = classic_client.read()

    epever_client = None
    if args.direct_epever:
        epever_client = EpeverClient(
            device=args.epever_device,
            baud=args.epever_baud,
            unit=args.epever_unit,
        )
        _, epever_obj = epever_client.read()
        epever = {
            "battery_type": epever_obj.battery_type,
            "battery_type_code": epever_obj.battery_type_code,
            "charging_limit_voltage_v": epever_obj.charging_limit_voltage_v,
            "boost_voltage_v": epever_obj.boost_voltage_v,
            "float_voltage_v": epever_obj.float_voltage_v,
            "equalize_voltage_v": epever_obj.equalize_voltage_v,
            "max_charging_current_a": epever_obj.max_charging_current_a,
        }
    else:
        epever = epever_settings_from_api(args.api_url)

    target_boost = round(classic.absorb_voltage_v + args.voltage_offset, 2)
    target_float = round(classic.float_voltage_v + args.voltage_offset, 2)
    target_equalize = round(max(classic.equalize_voltage_v + args.voltage_offset, target_boost), 2)

    # (label, target value, current epever value, API field)
    voltage_plan = [
        ("Boost (absorb)", target_boost, epever["boost_voltage_v"], "boost_voltage_v"),
        ("Float", target_float, epever["float_voltage_v"], "float_voltage_v"),
        ("Equalize", target_equalize, epever["equalize_voltage_v"], "equalize_voltage_v"),
    ]

    epever_target = args.epever_device if args.direct_epever else args.api_url.rstrip("/")
    print(f"Classic baseline @ {args.classic_host}  ->  EPEver via {'direct serial' if args.direct_epever else 'supervisor API'} @ {epever_target}")
    if args.voltage_offset:
        print(f"Voltage offset: +{args.voltage_offset:.2f}V applied to Classic voltage targets")
    print(f"EPEver Battery Type: {epever['battery_type']} (code {epever['battery_type_code']})")
    print(f"EPEver charging-limit voltage (ceiling): {_fmt(epever['charging_limit_voltage_v'])}")
    print()
    print(f"  {'Parameter':<16}{'Classic':>10}{'EPEver now':>14}{'->':>5}{'EPEver new':>14}")

    voltage_kwargs: dict[str, float] = {}
    blocked = False
    for label, source, current, kwarg in voltage_plan:
        change = "" if abs(source - current) < 0.01 else "  *"
        ceiling_hit = source > epever["charging_limit_voltage_v"] + 1e-9
        flag = "  !! exceeds charging-limit ceiling" if ceiling_hit else change
        print(f"  {label:<16}{_fmt(source):>10}{_fmt(current):>14}{'->':>5}{_fmt(source):>14}{flag}")
        if ceiling_hit:
            blocked = True
        elif abs(source - current) >= 0.01:
            voltage_kwargs[kwarg] = source

    current_change = None
    if not args.no_current:
        target_current = classic.battery_current_limit_a
        now_current = epever.get("max_charging_current_a")
        now_str = _fmt(now_current).replace("V", "A") if now_current is not None else "n/a"
        if now_current is None or abs(target_current - now_current) >= 0.1:
            current_change = target_current
        print(f"  {'Max charge A':<16}{target_current:>9.1f}A{now_str:>14}{'->':>5}{target_current:>13.1f}A")

    print()
    if blocked:
        print("ABORT: a target voltage exceeds the EPEver charging-limit ceiling (0x9008).")
        print("Raise the EPEver charging-limit voltage first, or reconcile the Classic setting.")
        return 2

    if not voltage_kwargs and current_change is None:
        print("Already aligned; nothing to write.")
        return 0

    if not args.write:
        print("Dry-run: no changes written. Re-run with --write to apply.")
        return 0

    if voltage_kwargs:
        if args.direct_epever:
            settings = epever_client.write_charge_voltages(
                boost_v=voltage_kwargs.get("boost_voltage_v"),
                float_v=voltage_kwargs.get("float_voltage_v"),
                equalize_v=voltage_kwargs.get("equalize_voltage_v"),
            )
            readback = {
                "equalize_voltage_v": settings.equalize_voltage_v,
                "boost_voltage_v": settings.boost_voltage_v,
                "float_voltage_v": settings.float_voltage_v,
            }
        else:
            response = api_json(
                args.api_url,
                "/api/v1/control/charge-controller/sync",
                {"source": 0, "target": 1, "voltage_offset_v": args.voltage_offset, "no_current": args.no_current},
            )
            readback = response["settings"]
            current_change = None
        print(
            "Wrote voltages -> "
            f"Equalize {_fmt(readback['equalize_voltage_v'])}  "
            f"Boost {_fmt(readback['boost_voltage_v'])}  "
            f"Float {_fmt(readback['float_voltage_v'])}"
        )
        if not args.direct_epever and not args.no_current and readback.get("max_charging_current_a") is not None:
            print(f"Wrote max charging current -> {readback['max_charging_current_a']:.1f}A")
    if current_change is not None:
        if args.direct_epever:
            settings = epever_client.write_max_charging_current(current_change)
            max_current = settings.max_charging_current_a
        else:
            settings = api_json(
                args.api_url,
                "/api/v1/control/charge-controller/charge-settings",
                {"controller": 1, "max_charging_current_a": current_change},
            )["settings"]
            max_current = settings["max_charging_current_a"]
        print(f"Wrote max charging current -> {max_current:.1f}A")

    print("Done. Verified by read-back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
