"""Calibrate PV effectiveness from uncurtailed intervals. Run on the Pi.

    sudo -u offgrid python3 scripts/calibrate_pv.py [start-date] [end-date]

Selects 10-minute bins where a controller is in Bulk with SOC <= 88% -- i.e.
production is sun-limited, not curtailment-limited -- and regresses delivered
charge power against irradiance. Reports two bases per array:

  * POA: plane-of-array irradiance reconstructed from the weather feed's
    direct-normal and diffuse components using the array's tilt/azimuth, and
    normalized to a 25 C cell temperature. **This is the basis the model
    uses.** A tilted array's POA/GHI ratio swings 2x across the year (0.93 in
    July, 1.86 in December at 45 S here), so a GHI coefficient fitted in
    summer badly understates winter -- see docs/power-budget.md.
  * GHI: horizontal irradiance, kept only to compare against the pre-2026-07-27
    numbers in the history section of that doc.

Each array is fitted independently, so a decommissioned or not-yet-installed
array simply reports no bins instead of zeroing the whole run.

Array geometry (docs/power-budget.md):
  array 0 (classic.0): 2.4 kW, 45 deg tilt, 180 deg azimuth, rooftop
  array 1 (epever.1):  3.6 kW, planned 45 deg / 180 deg on the cabin roof;
                       decommissioned 2026-07-18 until ~Sep 2027

The array 1 geometry above is the *planned* mount. It lay flat on the ground
through 2026-07-18, so any array 1 result over a window before the remount is
fitted against a plane-of-array irradiance the modules never saw and is not a
meaningful coefficient -- use it only as a relative before/after check.

Reference results: array 0 fitted 1.60 W/W/m2 POA at 25 C over 2026-06-20..07-02
(scaled x8/7 for the wiring fault) and 2.01 over 2026-07-19..27. That spread is
the open calibration problem, not noise -- read the doc before trusting either.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

DB = "file:/srv/telemetry/data/metrics.sqlite?mode=ro"
LAT, LON = 47.952, -84.841
GAMMA_P = -0.0041  # module power temperature coefficient, per degC
# `bulk_stages` is per-array because the controllers do not share a stage
# vocabulary. The Classic reports Bulk/BulkMppt. The EPEver folds bulk into
# "Boost", which the canonical mapping normalizes to *Absorb* -- so the EPEver
# never emits any Bulk word, and filtering it on the Classic's vocabulary
# silently yields zero bins. A zero-bin result reads like "no data" rather than
# "wrong filter", so it hides itself; that is what produced an empty
# recalibration run on 2026-07-26.
#
# Accepting Absorb for the EPEver therefore also admits genuine (voltage
# limited) absorb, which is curtailed rather than sun-limited. The SOC <= 88%
# gate is what actually excludes those: real absorb happens near full charge.
ARRAYS = {
    "array0": {"source": "classic.0", "kw": 2.4, "tilt": 45.0, "azimuth": 180.0,
               "bulk_stages": ("Bulk", "BulkMppt", "Bulk MPPT")},
    "array1": {"source": "epever.1", "kw": 3.6, "tilt": 45.0, "azimuth": 180.0,
               "bulk_stages": ("Bulk", "BulkMppt", "Bulk MPPT", "Boost", "Absorb")},
}


def _bucket(rows, agg="mean"):
    out = defaultdict(list)
    for captured_at, value in rows:
        if value is None:
            continue
        out[int(datetime.fromisoformat(captured_at).timestamp() // 600)].append(value)
    if agg == "list":
        return dict(out)
    return {k: sum(v) / len(v) for k, v in out.items()}


def _forward_fill(series, limit=3):
    """Weather updates every ~30 min; carry a value forward at most `limit` bins."""
    if not series:
        return series
    filled, last, age = {}, None, limit + 1
    for key in range(min(series), max(series) + 1):
        if key in series:
            last, age = series[key], 0
        else:
            age += 1
        if last is not None and age <= limit:
            filled[key] = last
    return filled


def _sun_position(ts):
    """Return (elevation, azimuth) in radians; NOAA low-precision algorithm."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    day = dt.timetuple().tm_yday
    hour = dt.hour + dt.minute / 60 + dt.second / 3600
    g = 2 * math.pi / 365 * (day - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
    )
    hour_angle = math.radians((hour * 60 + eqtime + 4 * LON) / 4 - 180)
    lat = math.radians(LAT)
    cos_zenith = max(-1.0, min(1.0, math.sin(lat) * math.sin(decl)
                               + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)))
    zenith = math.acos(cos_zenith)
    sin_zenith = math.sin(zenith)
    if sin_zenith < 1e-6:
        azimuth = 0.0
    else:
        cos_az = max(-1.0, min(1.0, (math.sin(decl) - math.sin(lat) * cos_zenith)
                               / (math.cos(lat) * sin_zenith)))
        azimuth = math.acos(cos_az)
        if hour_angle > 0:
            azimuth = 2 * math.pi - azimuth
    return math.pi / 2 - zenith, azimuth


def poa_irradiance(ts, tilt_deg, azimuth_deg, ghi, dhi, dni, albedo=0.2):
    elevation, sun_az = _sun_position(ts)
    tilt, panel_az = math.radians(tilt_deg), math.radians(azimuth_deg)
    sky = dhi * (1 + math.cos(tilt)) / 2
    ground = ghi * albedo * (1 - math.cos(tilt)) / 2
    if elevation <= 0:
        return sky + ground
    cos_aoi = max(0.0, (math.cos(elevation) * math.sin(sun_az) * math.sin(tilt) * math.sin(panel_az)
                        + math.cos(elevation) * math.cos(sun_az) * math.sin(tilt) * math.cos(panel_az)
                        + math.sin(elevation) * math.cos(tilt)))
    return dni * cos_aoi + sky + ground


def _fit(points):
    """Least-squares slope through the origin, with R^2."""
    denom = sum(x * x for x, _ in points)
    if not denom:
        return None, None
    slope = sum(x * y for x, y in points) / denom
    mean_y = sum(y for _, y in points) / len(points)
    ss_res = sum((y - slope * x) ** 2 for x, y in points)
    ss_tot = sum((y - mean_y) ** 2 for _, y in points)
    return slope, (1 - ss_res / ss_tot if ss_tot else None)


def calibrate(conn, start, end):
    def rows(source, metric, text=False):
        column = "text" if text else "value"
        return conn.execute(
            f"SELECT captured_at, {column} FROM samples "
            "WHERE source = ? AND metric = ? AND captured_at >= ? AND captured_at < ? "
            "ORDER BY captured_at",
            (source, metric, start, end),
        ).fetchall()

    soc = _bucket(rows("battery", "soc"))
    ghi = _forward_fill(_bucket(rows("weather", "shortwave_radiation")))
    dhi = _forward_fill(_bucket(rows("weather", "diffuse_radiation")))
    dni = _forward_fill(_bucket(rows("weather", "direct_normal_irradiance")))
    ambient = _forward_fill(_bucket(rows("weather", "temperature")), limit=6)

    results = {}
    for name, spec in ARRAYS.items():
        power = _bucket(rows(spec["source"], "battery_power"))
        stage = _bucket(rows(spec["source"], "charge_stage", text=True), agg="list")
        if not power:
            results[name] = {"bins": 0, "note": f"no {spec['source']} samples in window"}
            continue

        poa_points, ghi_points = [], []
        for key in sorted(set(power) & set(soc) & set(ghi) & set(dhi) & set(dni) & set(ambient)):
            stages = stage.get(key, [])
            in_bulk = all(s in spec["bulk_stages"] for s in stages) if stages else False
            if ghi[key] < 50 or soc[key] > 88 or not in_bulk:
                continue
            poa = poa_irradiance(key * 600 + 300, spec["tilt"], spec["azimuth"],
                                 ghi[key], dhi[key], dni[key])
            if poa < 50:
                continue
            cell_temp = ambient[key] + 0.03 * poa
            poa_points.append((poa, power[key] / (1 + GAMMA_P * (cell_temp - 25))))
            ghi_points.append((ghi[key], power[key]))

        if not poa_points:
            results[name] = {"bins": 0, "note": "no uncurtailed bulk bins in window"}
            continue

        poa_slope, poa_r2 = _fit(poa_points)
        ghi_slope, ghi_r2 = _fit(ghi_points)
        results[name] = {
            "bins": len(poa_points),
            "poa_slope_25c": round(poa_slope, 3),
            "poa_r2": round(poa_r2, 3) if poa_r2 is not None else None,
            "pct_of_nameplate": round(100 * poa_slope / spec["kw"]),
            "ghi_slope_legacy": round(ghi_slope, 3),
            "ghi_r2_legacy": round(ghi_r2, 3) if ghi_r2 is not None else None,
        }
        if poa_r2 is not None and poa_r2 < 0.6:
            results[name]["warning"] = (
                f"weak fit (R^2 {poa_r2:.2f}): modeled irradiance cannot track site cloud "
                "at 10-min resolution, so this slope depends on sample composition"
            )
    return results


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2026-07-19"
    end = sys.argv[2] if len(sys.argv) > 2 else "2100-01-01"
    try:
        conn = sqlite3.connect(DB, uri=True)
        conn.execute("SELECT 1 FROM samples LIMIT 1")
    except sqlite3.OperationalError as exc:
        sys.exit(f"cannot open telemetry database: {exc}\n"
                 "It is owned by 'offgrid' -- re-run with: sudo -u offgrid python3 ...")
    print(json.dumps({"window": [start, end], "arrays": calibrate(conn, start, end)}, indent=1))


if __name__ == "__main__":
    main()
