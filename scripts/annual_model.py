"""Annual power-balance model for the Wawa off-grid system.

    python3 scripts/annual_model.py [--k K] [--array1] [--cache DIR]

Models delivered DC energy as

    PV = k * POA(45 deg S) * temperature_factor

where POA is plane-of-array irradiance for the site (10-year Open-Meteo/ERA5
series, fetched and cached on first run) and `k` is the per-array coefficient
in W per W/m2 of POA at 25 C, calibrated by scripts/calibrate_pv.py.

Modeling a tilted array against *horizontal* irradiance -- as this script did
before 2026-07-27 -- understates December output by ~2x, because POA/GHI at
45 deg south runs 0.93 in July and 1.86 in December here. See
docs/power-budget.md.

`k` is deliberately a bracket, not a point estimate: successive calibration
windows do not agree (1.60 in June scaled for the wiring fault, 2.01 over
2026-07-19..27, 1.90 over 07-19..30), because the regression's independent
variable is modeled irradiance that cannot track site cloud. Runs print both
ends so the spread stays visible in the output rather than hiding in a mean.

Array 1 is decommissioned until ~Sep 2027; pass --array1 to model the
post-remount system.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import urllib.parse
import urllib.request
from collections import defaultdict

LAT, LON = 47.952, -84.841
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
YEARS = ("2016-01-01", "2025-12-31")
SEASONS = range(2016, 2025)  # complete winters, Oct-Apr

ARRAY0_KW, ARRAY1_KW = 2.4, 3.6
K_BRACKET = (1.60, 2.01)          # W per W/m2 POA at 25 C, array 0
ARRAY1_PR_BRACKET = (0.55, 0.70)  # fraction of nameplate, post-remount estimate
ARRAY1_DEC_BEAM = 0.15            # surveyed beam passage at the array 1 site

GAMMA_P = -0.0041                 # module power temp coefficient, per degC
BANK_USABLE_KWH = 10.24 * 0.85
HEATER_W = 200.0

LOADS = {                          # kWh/day, excluding the battery heater
    "full occupancy": 5.15,
    "no refrigeration": 4.38,      # 5.15 - metered 0.77
    "lean caretaker": 0.52,        # Pi/comms + one 60-min inverter+Starlink window
}


def _fetch(cache_dir, name, params):
    path = os.path.join(cache_dir, name)
    if os.path.exists(path):
        return json.load(open(path))
    os.makedirs(cache_dir, exist_ok=True)
    url = f"{ARCHIVE}?{urllib.parse.urlencode(params)}"
    print(f"fetching {name} ...")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            data = json.load(response)
    except urllib.error.URLError:
        # Some Python builds (notably python.org macOS) ship without a usable
        # CA bundle. curl is present on both the Mac and the Pi.
        import subprocess
        result = subprocess.run(["curl", "-sS", "--fail", url],
                                capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise SystemExit(f"fetch failed for {name}: {result.stderr.strip()}")
        data = json.loads(result.stdout)
    if "error" in data:
        raise SystemExit(f"Open-Meteo error for {name}: {data.get('reason')}")
    json.dump(data, open(path, "w"))
    return data


def load_days(cache_dir):
    """Per-day POA (kWh/m2), irradiance-weighted mean POA (W/m2), and ambient temp."""
    daily = _fetch(cache_dir, "daily.json", {
        "latitude": LAT, "longitude": LON, "start_date": YEARS[0], "end_date": YEARS[1],
        "daily": "temperature_2m_mean", "timezone": "America/Toronto"})
    temps = dict(zip(daily["daily"]["time"], daily["daily"]["temperature_2m_mean"]))

    poa_sum, poa_sqsum = defaultdict(float), defaultdict(float)
    for half, (start, end) in enumerate((("2016-01-01", "2020-12-31"), ("2021-01-01", "2025-12-31"))):
        block = _fetch(cache_dir, f"tilted{half}.json", {
            "latitude": LAT, "longitude": LON, "start_date": start, "end_date": end,
            "hourly": "global_tilted_irradiance", "tilt": 45, "azimuth": 0,
            "timezone": "America/Toronto"})
        hourly = block["hourly"]
        for stamp, value in zip(hourly["time"], hourly["global_tilted_irradiance"]):
            if value:
                poa_sum[stamp[:10]] += value
                poa_sqsum[stamp[:10]] += value * value

    days = []
    for date in sorted(poa_sum):
        temp = temps.get(date)
        if temp is None:
            continue
        weighted_poa = poa_sqsum[date] / poa_sum[date] if poa_sum[date] else 0.0
        cell_temp = temp + 0.03 * weighted_poa
        days.append({
            "date": date,
            "month": int(date[5:7]),
            "temp": temp,
            "poa_kwh": poa_sum[date] / 1000.0,
            "factor": 1 + GAMMA_P * (cell_temp - 25),
        })
    return days


def heater_kwh(temp):
    """200 W on a temperature-scaled duty: ~0 above +2 C, ~4 h/day at -10 C."""
    return HEATER_W / 1000 * min(12.0, max(0.0, (2.0 - temp) * 0.35))


def production(day, k, with_array1):
    total = k * day["poa_kwh"] * day["factor"]
    if with_array1:
        # array 1's surveyed site loses most December beam; scale its POA
        # accordingly (beam is ~68% of December POA at this tilt).
        beam_pass = ARRAY1_DEC_BEAM if day["month"] in (11, 12, 1, 2) else 1.0
        derate = 0.32 + 0.68 * beam_pass
        pr = sum(ARRAY1_PR_BRACKET) / 2
        total += ARRAY1_KW * pr * day["poa_kwh"] * day["factor"] * derate
    return total


def scenario(days, k, load, with_array1):
    nets = [production(d, k, with_array1) - (load + heater_kwh(d["temp"])) for d in days]
    dark = st.mean([production(d, k, with_array1) for d in days
                    if d["month"] == 12 and int(d["date"][8:10]) >= 15])

    worst7, deficit = {}, defaultdict(float)
    for i in range(len(days) - 6):
        season = _season(days[i]["date"])
        total = sum(nets[i:i + 7])
        if season in SEASONS and (season not in worst7 or total < worst7[season]):
            worst7[season] = total
    for day, net in zip(days, nets):
        season = _season(day["date"])
        if net < 0 and season in SEASONS:
            deficit[season] += -net

    soc, sessions = BANK_USABLE_KWH, defaultdict(int)
    for day, net in zip(days, nets):
        soc = min(BANK_USABLE_KWH, soc + net)
        if soc <= 0:
            sessions[_season(day["date"])] += 1
            soc = BANK_USABLE_KWH

    ordered = sorted(worst7.values())
    return {
        "dark_kwh_day": dark,
        "worst7_median": ordered[len(ordered) // 2],
        "worst7_min": ordered[0],
        "deficit_median": st.median(list(deficit.values()) or [0.0]),
        "generator_sessions": st.mean([sessions[s] for s in SEASONS]),
    }


def winter_soc(days, k, load, with_array1):
    """Unattended: no generator refill, so the bank can sit empty."""
    winters = defaultdict(list)
    for day in days:
        month = day["month"]
        if month >= 10:
            winters[int(day["date"][:4])].append(day)
        elif month <= 4:
            winters[int(day["date"][:4]) - 1].append(day)

    worst, empty = BANK_USABLE_KWH, 0
    for season in SEASONS:
        soc = BANK_USABLE_KWH
        for day in winters.get(season, []):
            soc = min(BANK_USABLE_KWH, soc + production(day, k, with_array1)
                      - (load + heater_kwh(day["temp"])))
            if soc <= 0:
                empty += 1
                soc = 0.0
            worst = min(worst, soc)
    return worst, empty


def _season(date):
    year, month = int(date[:4]), int(date[5:7])
    return year if month >= 7 else year - 1


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--k", type=float, action="append",
                        help="array 0 coefficient (W per W/m2 POA at 25 C); repeatable. "
                             f"Default: the calibrated bracket {K_BRACKET}")
    parser.add_argument("--array1", action="store_true",
                        help="include array 1 (post-remount, ~Sep 2027)")
    parser.add_argument("--cache", default=os.path.expanduser("~/.cache/offgrid-irradiance"),
                        help="directory for cached Open-Meteo data")
    args = parser.parse_args()

    days = load_days(args.cache)
    ks = args.k or list(K_BRACKET)
    label = "array 0 + array 1" if args.array1 else "array 0 only (array 1 decommissioned)"
    print(f"\n{label}; {len(days)} days, winters {min(SEASONS)}-{max(SEASONS) + 1}")
    print("Loads exclude the battery heater, which is added per-day from temperature.\n")

    for k in ks:
        print(f"k = {k:.2f} W per W/m2 POA at 25 C  ({100 * k / ARRAY0_KW:.0f}% of array 0 nameplate)")
        header = f"  {'mode':>18} {'darkPV':>7} {'w7 med':>8} {'w7 min':>8} {'deficit':>8} {'gen/wtr':>8}"
        print(header)
        for name, load in LOADS.items():
            r = scenario(days, k, load, args.array1)
            print(f"  {name:>18} {r['dark_kwh_day']:7.2f} {r['worst7_median']:8.1f} "
                  f"{r['worst7_min']:8.1f} {r['deficit_median']:8.0f} {r['generator_sessions']:8.1f}")
        worst, empty = winter_soc(days, k, LOADS["lean caretaker"], args.array1)
        print(f"  unattended lean caretaker: worst min SOC {100 * worst / BANK_USABLE_KWH:.0f}%, "
              f"{empty} empty days across {len(list(SEASONS))} winters\n")

    print("darkPV = mean Dec 15-31 production, kWh/day.  w7 = worst 7-day net, kWh "
          "(median / worst of 9 winters).\ndeficit = median winter sum of negative days, kWh.  "
          "gen/wtr = bank-empty events per winter when attended.")


if __name__ == "__main__":
    main()
