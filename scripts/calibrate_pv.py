"""Calibrate PV output vs GHI from uncurtailed intervals; run on the Pi.

Usage: sudo -u offgrid python3 calibrate_pv.py [start-date]  (default 2026-06-20)

Selects 10-minute bins where the system is in Bulk with SOC <= 88% (i.e.
production is sun-limited, not curtailment-limited) and regresses total
delivered charge power against the logged Open-Meteo GHI. The slopes are the
W-per-W/m2 effectiveness figures used by annual_model.py; re-run after any
array change (3s4p rewire 2026-07-02, platform mount) to update them.
Baseline 2026-06-20..07-02: total 1.84, classic.0 1.27, epever.1 0.57 (flat
on the ground)."""
import json, sys
from collections import defaultdict
from datetime import datetime

from sqlite_readonly import open_readonly_database

START = sys.argv[1] if len(sys.argv) > 1 else '2026-06-20'
conn = open_readonly_database('/srv/telemetry/data/metrics.sqlite')

def fetch(source, metric, textcol=False):
    col = 'text' if textcol else 'value'
    rows = conn.execute(
        f"SELECT captured_at, {col} FROM samples "
        "WHERE source=? AND metric=? AND captured_at>=? ORDER BY captured_at",
        (source, metric, START)).fetchall()
    return rows

def bucket(rows, agg='mean'):
    """10-min buckets keyed by unix//600."""
    b = defaultdict(list)
    for ts, v in rows:
        if v is None:
            continue
        t = datetime.fromisoformat(ts).timestamp()
        b[int(t // 600)].append(v)
    if agg == 'mean':
        return {k: sum(v)/len(v) for k, v in b.items()}
    return {k: v for k, v in b.items()}

pv0   = bucket(fetch('classic.0', 'battery_power'))
pv1   = bucket(fetch('epever.1',  'battery_power'))
pv1pv = bucket(fetch('epever.1',  'pv_power'))
soc   = bucket(fetch('battery',   'soc'))
ccl   = bucket(fetch('battery',   'charge_current_limit'))
vbat  = bucket(fetch('battery',   'voltage'))
ghi   = bucket(fetch('weather',   'shortwave_radiation'))
stage = bucket(fetch('classic.0', 'charge_stage', textcol=True), agg='list')

# weather updates ~every 30 min; forward-fill GHI into missing 10-min buckets (up to 3)
if ghi:
    filled = {}
    lo, hi = min(ghi), max(ghi)
    last, age = None, 99
    for k in range(lo, hi + 1):
        if k in ghi:
            last, age = ghi[k], 0
        else:
            age += 1
        if last is not None and age <= 3:
            filled[k] = last
    ghi = filled

keys = sorted(set(pv0) & set(pv1) & set(soc) & set(ghi) & set(vbat))
rows = []
for k in keys:
    st = stage.get(k, [])
    bulk = all(s in ('Bulk', 'BulkMppt', 'Bulk MPPT') for s in st) if st else False
    pv_tot = pv0[k] + pv1[k]
    ccl_w = (ccl.get(k, 0) or 0) * vbat[k]
    rows.append(dict(k=k, ghi=ghi[k], pv=pv_tot, pv0=pv0[k], pv1=pv1[k],
                     pv1_pvside=pv1pv.get(k), soc=soc[k], bulk=bulk, ccl_w=ccl_w))

sel = [r for r in rows if r['soc'] <= 88 and r['ghi'] >= 50 and r['bulk']]
# flag bins where charge power is near the CCL ceiling (allocator-limited)
limited = [r for r in sel if r['ccl_w'] > 0 and r['pv'] >= 0.85 * r['ccl_w']]
clean = [r for r in sel if r not in limited]

def slope(rs):
    num = sum(r['pv'] * r['ghi'] for r in rs)
    den = sum(r['ghi'] ** 2 for r in rs)
    return num / den if den else None

out = {
    'n_all_bins': len(rows), 'n_selected': len(sel), 'n_ccl_limited': len(limited),
    'slope_all_selected': slope(sel), 'slope_clean': slope(clean),
    'slope_pv0': (sum(r['pv0']*r['ghi'] for r in clean) / sum(r['ghi']**2 for r in clean)) if clean else None,
    'slope_pv1': (sum(r['pv1']*r['ghi'] for r in clean) / sum(r['ghi']**2 for r in clean)) if clean else None,
    'ccl_typical_w': sorted(r['ccl_w'] for r in rows if r['ccl_w'] > 0)[len([r for r in rows if r['ccl_w'] > 0]) // 2] if any(r['ccl_w'] > 0 for r in rows) else None,
    'sample_clean': [dict(ghi=round(r['ghi']), pv=round(r['pv']), pv0=round(r['pv0']),
                          pv1=round(r['pv1']), soc=r['soc']) for r in clean[:: max(1, len(clean)//25)]],
}
# daily delivered PV by integration (all bins, curtailed or not): mean W over day
daily = defaultdict(lambda: [0.0, 0])
for r in rows:
    day = datetime.fromtimestamp(r['k'] * 600).astimezone().date().isoformat()
    daily[day][0] += r['pv'] / 6.0  # 10-min bin -> Wh
    daily[day][1] += 1
out['daily_delivered_wh'] = {d: round(v[0]) for d, v in sorted(daily.items())}
print(json.dumps(out, indent=1))
