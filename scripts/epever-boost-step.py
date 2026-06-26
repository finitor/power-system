#!/usr/bin/env python3
"""Stage a small EPEver boost-voltage increase through the supervisor API.

Dry-run by default: reads the current EPEver charge settings from the
supervisor snapshot, prints the planned boost/equalize change, and writes
nothing. Pass --write to apply through the supervisor's EPEver actor thread.

The TEP rejects boost voltages above equalize voltage, so this tool carries
equalize upward to match the proposed boost when needed. It leaves float,
protection thresholds, and current limit untouched.

    .venv/bin/python scripts/epever-boost-step.py
    .venv/bin/python scripts/epever-boost-step.py --step-voltage 0.20
    .venv/bin/python scripts/epever-boost-step.py --target-boost-voltage 54.60
    .venv/bin/python scripts/epever-boost-step.py --write
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.config import load_config  # noqa: E402
from offgrid_power.epever import EpeverClient  # noqa: E402

DEFAULT_API_URL = "http://127.0.0.1:8081"


def supervisor_active() -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "offgrid-supervisor"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() == "active"
    except Exception:  # noqa: BLE001 - if we can't tell, don't block a read
        return False


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default=config.epever.device)
    parser.add_argument("--baud", type=int, default=config.epever.baud)
    parser.add_argument("--unit", type=int, default=config.epever.unit)
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="supervisor base URL")
    parser.add_argument("--direct", action="store_true", help="open the EPEver serial device directly")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--step-voltage",
        type=float,
        default=0.10,
        help="boost-voltage increase in volts (default: 0.10)",
    )
    target.add_argument("--target-boost-voltage", type=float, help="absolute boost-voltage target")
    parser.add_argument("--write", action="store_true", help="apply the planned change (default is dry-run)")
    parser.add_argument("--force", action="store_true", help="run even if the supervisor is active")
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


def read_api_settings(base_url: str) -> dict:
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
    if args.direct and supervisor_active() and not args.force:
        print("Refusing: offgrid-supervisor is active and owns the adapter.", file=sys.stderr)
        print("Stop it first (sudo systemctl stop offgrid-supervisor) or pass --force.", file=sys.stderr)
        return 1

    if args.target_boost_voltage is None and args.step_voltage <= 0:
        print("Refusing: --step-voltage must be positive.", file=sys.stderr)
        return 2

    client = EpeverClient(device=args.device, baud=args.baud, unit=args.unit) if args.direct else None
    if args.direct:
        _, settings_obj = client.read()
        settings = {
            "battery_type": settings_obj.battery_type,
            "battery_type_code": settings_obj.battery_type_code,
            "charging_limit_voltage_v": settings_obj.charging_limit_voltage_v,
            "equalize_voltage_v": settings_obj.equalize_voltage_v,
            "boost_voltage_v": settings_obj.boost_voltage_v,
            "float_voltage_v": settings_obj.float_voltage_v,
        }
    else:
        settings = read_api_settings(args.api_url)

    target_boost = (
        args.target_boost_voltage
        if args.target_boost_voltage is not None
        else settings["boost_voltage_v"] + args.step_voltage
    )
    target_boost = round(target_boost, 2)
    target_equalize = max(settings["equalize_voltage_v"], target_boost)

    if settings["battery_type"] != "User":
        print(
            "ABORT: EPEver Battery Type must be User before charge-voltage writes; "
            f"controller reports {settings['battery_type']} (code {settings['battery_type_code']}).",
            file=sys.stderr,
        )
        return 2
    if target_boost <= settings["boost_voltage_v"]:
        print(
            "ABORT: target boost must be above the current boost voltage "
            f"({_fmt(settings['boost_voltage_v'])}).",
            file=sys.stderr,
        )
        return 2
    if target_equalize > settings["charging_limit_voltage_v"]:
        print(
            "ABORT: planned equalize/boost exceeds the charging-limit ceiling: "
            f"{_fmt(target_equalize)} > {_fmt(settings['charging_limit_voltage_v'])}.",
            file=sys.stderr,
        )
        return 2

    location = args.device if args.direct else args.api_url.rstrip("/")
    print(f"EPEver via {'direct serial' if args.direct else 'supervisor API'} @ {location}")
    print(f"Battery Type: {settings['battery_type']} (code {settings['battery_type_code']})")
    print(f"Charging-limit ceiling: {_fmt(settings['charging_limit_voltage_v'])}")
    print()
    print(f"  {'Parameter':<10}{'Now':>10}{'->':>5}{'Planned':>10}")
    print(f"  {'Boost':<10}{_fmt(settings['boost_voltage_v']):>10}{'->':>5}{_fmt(target_boost):>10}  *")
    eq_mark = "  * follows boost" if target_equalize != settings["equalize_voltage_v"] else ""
    print(f"  {'Equalize':<10}{_fmt(settings['equalize_voltage_v']):>10}{'->':>5}{_fmt(target_equalize):>10}{eq_mark}")
    print(f"  {'Float':<10}{_fmt(settings['float_voltage_v']):>10}{'->':>5}{_fmt(settings['float_voltage_v']):>10}")
    print()

    if not args.write:
        print("Dry-run: no changes written. Re-run with --write to apply.")
        return 0

    if args.direct:
        readback = client.write_charge_voltages(boost_v=target_boost, equalize_v=target_equalize)
        readback_settings = {
            "equalize_voltage_v": readback.equalize_voltage_v,
            "boost_voltage_v": readback.boost_voltage_v,
            "float_voltage_v": readback.float_voltage_v,
        }
    else:
        response = api_json(
            args.api_url,
            "/api/v1/control/charge-controller/charge-settings",
            {"controller": 1, "boost_voltage_v": target_boost, "equalize_voltage_v": target_equalize},
        )
        readback_settings = response["settings"]
    print(
        "Wrote voltages -> "
        f"Equalize {_fmt(readback_settings['equalize_voltage_v'])}  "
        f"Boost {_fmt(readback_settings['boost_voltage_v'])}  "
        f"Float {_fmt(readback_settings['float_voltage_v'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
