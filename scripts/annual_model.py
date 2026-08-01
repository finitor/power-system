"""Historical power-balance model for the Wawa off-grid system.

    python3 scripts/annual_model.py [--k K] [--array1] [--cache DIR]

The model uses hourly, consistently sourced ERA5 weather. Array 0 production is
``k * POA(45 deg south) * temperature_factor``. The calibrated ``k`` remains a
bracket because summer telemetry is not a reliable point calibration for
winter. Array 1 results are provisional until its 2027 commissioning data exist.

Attended scenarios model the manual generator at 3.2 kW: start at a configurable
SOC threshold (20% default) and run to 90%. Generator power is treated as DC-bus
energy; conversion losses are not yet measured, so runtime is optimistic by the
unknown generator-to-DC loss. Unattended scenarios never use the generator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics as st
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from site_location import site_coordinates

LAT, LON, _COORD_SOURCE = site_coordinates()
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_MODEL = "era5"
YEARS = ("2016-01-01", "2025-12-31")
SEASONS = range(2016, 2025)  # complete winters, Oct-Apr

ARRAY0_KW, ARRAY1_KW = 2.4, 3.6
K_BRACKET = (1.60, 2.01)
ARRAY1_PR_BRACKET = (0.55, 0.70)
ARRAY1_DEC_BEAM = 0.15

GAMMA_P = -0.0041
BANK_USABLE_KWH = 10.24 * 0.85
HEATER_W = 200.0
GENERATOR_KW = 3.2
GENERATOR_START_SOC = 0.20
GENERATOR_STOP_SOC = 0.90

LOADS = {
    "full occupancy": 5.15,
    "no refrigeration": 4.38,
    "lean caretaker": 0.52,
}


def _fetch(cache_dir, name, params):
    """Fetch and cache one Open-Meteo response, keyed by all model inputs."""
    identity = {
        "latitude": round(params["latitude"], 3),
        "longitude": round(params["longitude"], 3),
        "model": params.get("models"),
        "start": params.get("start_date"),
        "end": params.get("end_date"),
        "hourly": params.get("hourly"),
        "tilt": params.get("tilt"),
        "azimuth": params.get("azimuth"),
    }
    tag = urllib.parse.quote(json.dumps(identity, sort_keys=True), safe="")
    # Keep filenames manageable while retaining the human-readable model name.
    digest = hashlib.sha256(tag.encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, f"{WEATHER_MODEL}-{digest}-{name}")
    if os.path.exists(path):
        with open(path) as handle:
            return json.load(handle)
    os.makedirs(cache_dir, exist_ok=True)
    url = f"{ARCHIVE}?{urllib.parse.urlencode(params)}"
    print(f"fetching {name} ({WEATHER_MODEL}) ...")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            data = json.load(response)
    except urllib.error.URLError:
        import subprocess
        result = subprocess.run(["curl", "-sS", "--fail", url],
                                capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise SystemExit(f"fetch failed for {name}: {result.stderr.strip()}")
        data = json.loads(result.stdout)
    if "error" in data:
        raise SystemExit(f"Open-Meteo error for {name}: {data.get('reason')}")
    data["_offgrid_request"] = identity
    with open(path, "w") as handle:
        json.dump(data, handle)
    return data


def load_hours(cache_dir):
    """Return hourly local-time POA and ambient temperature from pinned ERA5."""
    hours = []
    ranges = (("2016-01-01", "2020-12-31"), ("2021-01-01", "2025-12-31"))
    for half, (start, end) in enumerate(ranges):
        block = _fetch(cache_dir, f"hourly{half}.json", {
            "latitude": LAT,
            "longitude": LON,
            "start_date": start,
            "end_date": end,
            "hourly": "global_tilted_irradiance,temperature_2m",
            "tilt": 45,
            "azimuth": 0,
            "timezone": "America/Toronto",
            "models": WEATHER_MODEL,
        })
        hourly = block["hourly"]
        for stamp, poa, temp in zip(hourly["time"],
                                    hourly["global_tilted_irradiance"],
                                    hourly["temperature_2m"]):
            if poa is None or temp is None:
                continue
            poa = max(0.0, float(poa))
            cell_temp = float(temp) + 0.03 * poa
            hours.append({
                "stamp": stamp,
                "date": stamp[:10],
                "month": int(stamp[5:7]),
                "temp": float(temp),
                "poa_w": poa,
                "factor": max(0.0, 1 + GAMMA_P * (cell_temp - 25)),
            })
    return hours


def heater_kwh(temp):
    """Estimated daily heater energy from daily mean ambient temperature."""
    return HEATER_W / 1000 * min(12.0, max(0.0, (2.0 - temp) * 0.35))


def _daily_temperatures(hours):
    values = defaultdict(list)
    for hour in hours:
        values[hour["date"]].append(hour["temp"])
    return {date: st.mean(temps) for date, temps in values.items()}


def production(hour, k, array1_pr=None, winter_pv_factor=1.0):
    """Delivered DC energy during one hourly weather interval, in kWh."""
    total = k * hour["poa_w"] / 1000.0 * hour["factor"]
    if array1_pr is not None:
        beam_pass = ARRAY1_DEC_BEAM if hour["month"] in (11, 12, 1, 2) else 1.0
        derate = 0.32 + 0.68 * beam_pass
        total += (ARRAY1_KW * array1_pr * hour["poa_w"] / 1000.0
                  * hour["factor"] * derate)
    if hour["month"] in (10, 11, 12, 1, 2, 3, 4):
        total *= winter_pv_factor
    return total


def _winter_season(date):
    year, month = int(date[:4]), int(date[5:7])
    if month >= 10:
        return year
    if month <= 4:
        return year - 1
    return None


def _hourly_records(hours, k, load, array1_pr=None, heater_multiplier=1.0,
                    winter_pv_factor=1.0):
    daily_temp = _daily_temperatures(hours)
    for hour in hours:
        heater = heater_multiplier * heater_kwh(daily_temp[hour["date"]]) / 24.0
        pv = production(hour, k, array1_pr, winter_pv_factor)
        demand = load / 24.0 + heater
        yield hour, pv, demand, pv - demand


def _run_generator_hour(soc, natural_net, generator_on, start_kwh, stop_kwh,
                        generator_kw):
    """Advance one hour, including fractional start/stop within the interval."""
    sessions = runtime = generator_energy = unserved = 0.0
    remaining = 1.0

    if not generator_on and natural_net < 0 and soc + natural_net <= start_kwh:
        to_start = max(0.0, min(1.0, (soc - start_kwh) / -natural_net))
        soc += natural_net * to_start
        remaining -= to_start
        generator_on = True
        sessions = 1.0

    if generator_on:
        rate = natural_net + generator_kw
        if rate <= 0:
            runtime = remaining
            generator_energy = generator_kw * remaining
            soc += rate * remaining
        else:
            time_to_stop = max(0.0, (stop_kwh - soc) / rate)
            run_for = min(remaining, time_to_stop)
            runtime = run_for
            generator_energy = generator_kw * run_for
            soc += rate * run_for
            remaining -= run_for
            if time_to_stop <= run_for + 1e-12:
                soc = stop_kwh
                generator_on = False
                soc += natural_net * remaining
    else:
        soc += natural_net * remaining

    if soc < 0:
        unserved = -soc
        soc = 0.0
    soc = min(BANK_USABLE_KWH, soc)
    return soc, generator_on, sessions, runtime, generator_energy, unserved


def scenario(hours, k, load, array1_pr=None, generator_kw=GENERATOR_KW,
             generator_start_soc=GENERATOR_START_SOC,
             generator_stop_soc=GENERATOR_STOP_SOC,
             heater_multiplier=1.0, winter_pv_factor=1.0):
    """Run attended Oct-Apr winters with the manual-generator policy."""
    records = list(_hourly_records(hours, k, load, array1_pr,
                                   heater_multiplier, winter_pv_factor))
    daily_net, daily_pv = defaultdict(float), defaultdict(float)
    for hour, pv, _demand, net in records:
        daily_net[hour["date"]] += net
        daily_pv[hour["date"]] += pv

    dark_values = [pv for date, pv in daily_pv.items()
                   if date[5:7] == "12" and int(date[8:10]) >= 15]
    winter_dates = defaultdict(list)
    for date in sorted(daily_net):
        season = _winter_season(date)
        if season in SEASONS:
            winter_dates[season].append(date)

    worst7, net_energy, gross_negative = {}, {}, {}
    for season, dates in winter_dates.items():
        values = [daily_net[date] for date in dates]
        worst7[season] = min(sum(values[i:i + 7])
                             for i in range(max(1, len(values) - 6)))
        net_energy[season] = sum(values)
        gross_negative[season] = sum(-value for value in values if value < 0)

    sessions = defaultdict(int)
    runtime = defaultdict(float)
    generator_energy = defaultdict(float)
    unserved = defaultdict(float)
    min_soc = defaultdict(lambda: BANK_USABLE_KWH)
    state = {}
    start_kwh = BANK_USABLE_KWH * generator_start_soc
    stop_kwh = BANK_USABLE_KWH * generator_stop_soc
    for hour, _pv, _demand, net in records:
        season = _winter_season(hour["date"])
        if season not in SEASONS:
            continue
        soc, generator_on = state.get(season, (BANK_USABLE_KWH, False))
        result = _run_generator_hour(soc, net, generator_on, start_kwh,
                                     stop_kwh, generator_kw)
        soc, generator_on, starts, run, energy, lost = result
        state[season] = soc, generator_on
        sessions[season] += int(starts)
        runtime[season] += run
        generator_energy[season] += energy
        unserved[season] += lost
        min_soc[season] = min(min_soc[season], soc)

    ordered_worst7 = sorted(worst7.values())
    return {
        "dark_kwh_day": st.mean(dark_values),
        "worst7_median": st.median(ordered_worst7),
        "worst7_min": min(ordered_worst7),
        "winter_net_median": st.median(net_energy.values()),
        "gross_negative_median": st.median(gross_negative.values()),
        "generator_sessions": st.mean(sessions[s] for s in SEASONS),
        "generator_kwh": st.mean(generator_energy[s] for s in SEASONS),
        "generator_hours": st.mean(runtime[s] for s in SEASONS),
        "min_soc": min(min_soc.values()) / BANK_USABLE_KWH,
        "unserved_kwh": sum(unserved.values()),
    }


def winter_soc(hours, k, load, array1_pr=None, heater_multiplier=1.0,
               winter_pv_factor=1.0):
    """Simulate unattended Oct-Apr winters with no generator."""
    states = {season: BANK_USABLE_KWH for season in SEASONS}
    worst, empty_hours = BANK_USABLE_KWH, 0
    for hour, _pv, _demand, net in _hourly_records(
            hours, k, load, array1_pr, heater_multiplier, winter_pv_factor):
        season = _winter_season(hour["date"])
        if season not in SEASONS:
            continue
        soc = min(BANK_USABLE_KWH, states[season] + net)
        if soc <= 0:
            empty_hours += 1
            soc = 0.0
        states[season] = soc
        worst = min(worst, soc)
    return worst, empty_hours


def _print_scenario_table(hours, k, array1_pr, args):
    label = f"; array 1 PR={array1_pr:.2f} PROVISIONAL" if array1_pr is not None else ""
    print(f"k={k:.2f} ({100*k/ARRAY0_KW:.0f}% Array 0 nameplate){label}")
    print(f"  {'mode':>18} {'darkPV':>7} {'w7med':>7} {'net/wtr':>8} "
          f"{'negdays':>8} {'gen#':>6} {'genkWh':>7} {'genh':>6} {'minSOC':>7}")
    for name, load in LOADS.items():
        result = scenario(hours, k, load, array1_pr,
                          generator_kw=args.generator_kw,
                          generator_start_soc=args.generator_start_soc,
                          generator_stop_soc=args.generator_stop_soc)
        print(f"  {name:>18} {result['dark_kwh_day']:7.2f} {result['worst7_median']:7.1f} "
              f"{result['winter_net_median']:8.0f} {result['gross_negative_median']:8.0f} "
              f"{result['generator_sessions']:6.1f} {result['generator_kwh']:7.0f} "
              f"{result['generator_hours']:6.1f} {100*result['min_soc']:6.0f}%")
    worst, empty = winter_soc(hours, k, LOADS["lean caretaker"], array1_pr)
    print(f"  unattended lean: worst SOC {100*worst/BANK_USABLE_KWH:.0f}%, "
          f"{empty} empty hours across {len(SEASONS)} winters\n")


def _print_stress_cases(hours):
    print("Lean unattended stress cases (Array 0 only, low calibration k=1.60):")
    print(f"  {'case':>26} {'minSOC':>7} {'empty hours':>11}")
    cases = (
        ("baseline", 1.0, 1.0),
        ("heater 2x", 2.0, 1.0),
        ("winter PV 75%", 1.0, 0.75),
        ("heater 2x + PV 75%", 2.0, 0.75),
    )
    for name, heater_factor, pv_factor in cases:
        worst, empty = winter_soc(hours, K_BRACKET[0], LOADS["lean caretaker"],
                                  heater_multiplier=heater_factor,
                                  winter_pv_factor=pv_factor)
        print(f"  {name:>26} {100*worst/BANK_USABLE_KWH:6.0f}% {empty:11d}")


def main():
    global BANK_USABLE_KWH
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--k", type=float, action="append",
                        help=f"Array 0 coefficient; repeatable. Default: {K_BRACKET}")
    parser.add_argument("--array1", action="store_true",
                        help="include provisional 2027 Array 1 bracket")
    parser.add_argument("--cache", default=os.path.expanduser("~/.cache/offgrid-irradiance"))
    parser.add_argument("--generator-kw", type=float, default=GENERATOR_KW,
                        help=f"generator output delivered to DC bus (default {GENERATOR_KW})")
    parser.add_argument("--generator-start-soc", type=float, default=GENERATOR_START_SOC)
    parser.add_argument("--generator-stop-soc", type=float, default=GENERATOR_STOP_SOC)
    parser.add_argument("--bank-usable-kwh", type=float, default=BANK_USABLE_KWH,
                        help=f"usable battery energy (default {BANK_USABLE_KWH:.3f} kWh)")
    parser.add_argument("--no-stress", action="store_true",
                        help="omit unattended uncertainty table")
    args = parser.parse_args()
    if not 0 <= args.generator_start_soc < args.generator_stop_soc <= 1:
        parser.error("generator SOC thresholds must satisfy 0 <= start < stop <= 1")
    if args.generator_kw <= 0:
        parser.error("--generator-kw must be positive")
    if args.bank_usable_kwh <= 0:
        parser.error("--bank-usable-kwh must be positive")
    BANK_USABLE_KWH = args.bank_usable_kwh

    hours = load_hours(args.cache)
    ks = args.k or list(K_BRACKET)
    array1_prs = list(ARRAY1_PR_BRACKET) if args.array1 else [None]
    print(f"\nPinned weather={WEATHER_MODEL}; {len(hours)} hourly intervals; "
          f"winters {min(SEASONS)}-{max(SEASONS)+1}")
    print(f"Generator={args.generator_kw:.1f} kW DC-bus equivalent, manual start at "
          f"{100*args.generator_start_soc:.0f}% SOC, stop at {100*args.generator_stop_soc:.0f}%.")
    print(f"Usable battery={BANK_USABLE_KWH:.2f} kWh.")
    print("Loads and heater are spread evenly within each day; generator conversion losses "
          "are not yet measured.\n")
    if args.array1:
        print("WARNING: Array 1 outputs are design scenarios only; recalibrate after summer 2027 commissioning.\n")
    for array1_pr in array1_prs:
        for k in ks:
            _print_scenario_table(hours, k, array1_pr, args)
    if not args.no_stress and not args.array1:
        _print_stress_cases(hours)
    print("\nnet/wtr = median Oct-Apr net energy. negdays = median sum of negative daily "
          "balances (storage-shifting pressure, not generator energy). Generator columns are "
          "from the stated SOC policy; minSOC is the minimum attended end-of-hour SOC.")


if __name__ == "__main__":
    main()
