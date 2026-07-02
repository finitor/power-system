"""Annual power-balance model for the Wawa off-grid system.

PV potential = R * GHI_daily * temp_factor, with R calibrated from
uncurtailed telemetry by scripts/calibrate_pv.py. Load from measured DC-bus
consumption. See docs/journal/2026-07-02.md for the full analysis (winter
deficit, lean caretaker mode, AR shading survey, 3s4p decision).

Requires irradiance-2016-2025.json in the working directory:

  curl -s 'https://archive-api.open-meteo.com/v1/archive?latitude=47.952&longitude=-84.841&start_date=2016-01-01&end_date=2025-12-31&daily=shortwave_radiation_sum,snowfall_sum,temperature_2m_mean&timezone=America%2FToronto' -o irradiance-2016-2025.json

For mounted-array (45-degree south) scenarios, hourly plane-of-array
irradiance comes from the same API with
hourly=global_tilted_irradiance&tilt=45&azimuth=0.
"""
import json, statistics as st
from collections import defaultdict
from datetime import date

D = json.load(open('irradiance-2016-2025.json'))['daily']

# calibration (June 2026, cell temp warm). CS6X poly gamma ~ -0.41 %/C.
R_BOTH = 1.84        # W per W/m2, both arrays as currently deployed
R_ARR0 = 1.27        # array 0 only (array 1 snowed over / offline)
GAMMA = 0.0041
T_CAL_CELL = 40.0    # approx June midday cell temp during calibration

LOAD_KWH = 5.15      # measured 2026-06-20..07-02 mean (214 W)
HEATER_KWH_COLD = 0.8  # 200 W heater, VOC-gated, est ~4 h/day when Tmean < -5C
BANK_USABLE_KWH = 10.24 * 0.85

def cell_temp(t_amb, ghi_wm2_mean):
    # crude NOCT-ish: cell = ambient + 25C * (midday GHI / 800), midday ~ 2.4x daily mean
    return t_amb + 25.0 * min(1.2, 2.4 * ghi_wm2_mean / 800.0)

def temp_factor(t_amb, ghi_wm2_mean):
    return 1.0 + GAMMA * (T_CAL_CELL - cell_temp(t_amb, ghi_wm2_mean))

# organize by (year, doy-week): use ISO-agnostic fixed weeks: doy//7
by_week = defaultdict(lambda: defaultdict(list))  # week -> year -> [day dicts]
for i, ds in enumerate(D['time']):
    y, m, d = map(int, ds.split('-'))
    dt = date(y, m, d)
    doy = dt.timetuple().tm_yday
    if doy > 364:
        doy = 364
    wk = (doy - 1) // 7  # 0..51
    ghi_mj = D['shortwave_radiation_sum'][i]
    if ghi_mj is None:
        continue
    t = D['temperature_2m_mean'][i]
    snow = D['snowfall_sum'][i] or 0.0
    ghi_kwh = ghi_mj / 3.6
    ghi_wm2_mean = ghi_mj * 1e6 / 86400
    by_week[wk][y].append(dict(ghi_kwh=ghi_kwh, t=t, snow=snow,
                               tf=temp_factor(t, ghi_wm2_mean)))

def pv_day(day, r):
    return r * day['ghi_kwh'] * day['tf']

def load_day(day):
    return LOAD_KWH + (HEATER_KWH_COLD if day['t'] < -5 else 0.0)

rows = []
for wk in sorted(by_week):
    weekly_net_both, weekly_net_arr0, weekly_pv_both, weekly_ghi, weekly_t, weekly_snow = [], [], [], [], [], []
    for y, days in by_week[wk].items():
        pvb = sum(pv_day(d, R_BOTH) for d in days) / len(days)
        pv0 = sum(pv_day(d, R_ARR0) for d in days) / len(days)
        ld = sum(load_day(d) for d in days) / len(days)
        weekly_pv_both.append(pvb)
        weekly_net_both.append(pvb - ld)
        weekly_net_arr0.append(pv0 - ld)
        weekly_ghi.append(sum(d['ghi_kwh'] for d in days) / len(days))
        weekly_t.append(sum(d['t'] for d in days) / len(days))
        weekly_snow.append(sum(d['snow'] for d in days))
    mid = date(2025, 1, 1).fromordinal(date(2025, 1, 1).toordinal() + wk * 7 + 3)
    rows.append(dict(
        wk=wk, label=mid.strftime('%b %d'),
        ghi=st.mean(weekly_ghi),
        t=st.mean(weekly_t), snow_cm=st.mean(weekly_snow),
        pv_mean=st.mean(weekly_pv_both),
        net_mean=st.mean(weekly_net_both),
        net_min=min(weekly_net_both),
        net_arr0_mean=st.mean(weekly_net_arr0),
        net_arr0_min=min(weekly_net_arr0),
    ))

print(f"{'wk':>3} {'mid':>7} {'GHI':>5} {'T':>6} {'snow':>5} {'PVpot':>6} "
      f"{'net':>6} {'netMin':>7} {'net(a0)':>8} {'a0Min':>7}")
for r in rows:
    flag = ' <<<' if r['net_mean'] < 0 else ''
    print(f"{r['wk']:>3} {r['label']:>7} {r['ghi']:5.2f} {r['t']:6.1f} {r['snow_cm']:5.1f} "
          f"{r['pv_mean']:6.2f} {r['net_mean']:6.2f} {r['net_min']:7.2f} "
          f"{r['net_arr0_mean']:8.2f} {r['net_arr0_min']:7.2f}{flag}")

# darkest-week focus + deficit-season accounting per year
print("\n--- per-year December/January darkest stretch (both arrays, heater on) ---")
years = defaultdict(list)  # continuous daily series
for i, ds in enumerate(D['time']):
    ghi_mj = D['shortwave_radiation_sum'][i]
    if ghi_mj is None:
        continue
    t = D['temperature_2m_mean'][i]
    ghi_wm2_mean = ghi_mj * 1e6 / 86400
    day = dict(ds=ds, ghi_kwh=ghi_mj/3.6, t=t, tf=temp_factor(t, ghi_wm2_mean))
    years['all'].append(day)

series = years['all']
# worst 7-day and 14-day rolling net in each winter
def rolling_worst(days, r, window):
    worst = {}
    nets = [pv_day(d, r) - load_day(d) for d in days]
    for i in range(len(days) - window + 1):
        s = sum(nets[i:i+window])
        # attribute to winter season year (Dec belongs to that year's winter)
        ds = days[i]['ds']
        y, m, _ = map(int, ds.split('-'))
        season = y if m >= 7 else y - 1
        if season not in worst or s < worst[season][0]:
            worst[season] = (s, ds)
    return worst

for window in (7, 14):
    w_both = rolling_worst(series, R_BOTH, window)
    w_a0 = rolling_worst(series, R_ARR0, window)
    print(f"\nworst rolling {window}-day net (kWh): both-arrays | array0-only")
    for season in sorted(w_both):
        s1, d1 = w_both[season]
        s2, d2 = w_a0[season]
        print(f"  winter {season}-{season+1}: {s1:7.1f} @ {d1} | {s2:7.1f} @ {d2}")

# longest run of consecutive net-negative days per winter (both arrays)
print("\nlongest consecutive-deficit run and total winter deficit (both arrays, heater on):")
run, runs = 0, defaultdict(lambda: [0, 0.0])  # season -> [max run, total deficit kWh]
for d in series:
    net = pv_day(d, R_BOTH) - load_day(d)
    y, m, _ = map(int, d['ds'].split('-'))
    season = y if m >= 7 else y - 1
    if net < 0:
        run += 1
        runs[season][1] += -net
    else:
        run = 0
    runs[season][0] = max(runs[season][0], run)
for season in sorted(runs):
    mr, tot = runs[season]
    if tot > 0.5:
        print(f"  winter {season}-{season+1}: max run {mr:3d} days, total deficit {tot:7.1f} kWh")

print(f"\nbank usable ~{BANK_USABLE_KWH:.1f} kWh -> autonomy at winter load "
      f"({LOAD_KWH + HEATER_KWH_COLD:.1f} kWh/d): {BANK_USABLE_KWH/(LOAD_KWH+HEATER_KWH_COLD):.1f} days")
