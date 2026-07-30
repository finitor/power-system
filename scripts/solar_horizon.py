"""Solar geometry for siting decisions: how much does a horizon obstruction cost?

    python3 scripts/solar_horizon.py --clear 09:00-15:45
    python3 scripts/solar_horizon.py --clear 09:00-15:45 --tilt 45 --azimuth 180
    python3 scripts/solar_horizon.py --ratios

Answers the question an AR sun-path survey raises: the app shows *when* the
winter sun clears the treeline, but not what that is worth. Beam energy is
heavily concentrated near solar noon, so a window that looks like it loses two
hours may lose only a few percent of the energy.

Worked example (array 0 rooftop survey, 2026-07-27): the solstice path was
clear 09:00-15:45, which is 96.3% of solstice beam energy -- a 2-4% haircut on
winter plane-of-array once diffuse is included. The array 1 ground platform, by
contrast, loses 12:30-14:30 to the building, which is 35.7% of beam energy on
its own. Same survey method, order-of-magnitude different answer.

--ratios prints the monthly plane-of-array to horizontal ratio, which is why
the model is POA-based: at 45 deg south here it runs 0.90 in June and 1.86 in
December, so a summer-calibrated GHI coefficient understates winter by ~2x.

Uses a clear-sky beam model, so results are geometry -- the fraction of a
*sunny* solstice day's direct beam that a window captures. Real cloud cover
scales both sides and largely cancels.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from site_location import site_coordinates  # noqa: E402

LAT, LON, _ = site_coordinates()
SOLAR_CONSTANT = 1361.0


def sun_position(when):
    """(elevation, azimuth) in radians for a UTC datetime. NOAA low-precision."""
    day = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    g = 2 * math.pi / 365 * (day - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
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


def clear_sky_dni(elevation):
    """Direct-normal irradiance under clear sky, simple air-mass attenuation."""
    if elevation <= 0:
        return 0.0
    air_mass = 1 / (math.sin(elevation)
                    + 0.50572 * (math.degrees(elevation) + 6.07995) ** -1.6364)
    return SOLAR_CONSTANT * 0.7 ** (air_mass ** 0.678)


def cos_incidence(elevation, azimuth, tilt_deg, panel_azimuth_deg):
    tilt, panel = math.radians(tilt_deg), math.radians(panel_azimuth_deg)
    return max(0.0, math.cos(elevation) * math.sin(azimuth) * math.sin(tilt) * math.sin(panel)
               + math.cos(elevation) * math.cos(azimuth) * math.sin(tilt) * math.cos(panel)
               + math.sin(elevation) * math.cos(tilt))


def day_profile(date, tilt, azimuth, utc_offset_hours):
    """Minute-by-minute beam irradiance on the panel for one day."""
    base = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    profile = []
    for minute in range(1440):
        when = base + timedelta(minutes=minute)
        elevation, sun_az = sun_position(when)
        if elevation <= 0:
            continue
        beam = clear_sky_dni(elevation) * cos_incidence(elevation, sun_az, tilt, azimuth)
        local = (when + timedelta(hours=utc_offset_hours)).replace(tzinfo=None)
        profile.append((local, beam, math.degrees(elevation)))
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clear", action="append", metavar="HH:MM-HH:MM",
                        help="unobstructed window from a sun-path survey; repeatable")
    parser.add_argument("--date", default="12-21", help="MM-DD to evaluate (default winter solstice)")
    parser.add_argument("--tilt", type=float, default=45.0)
    parser.add_argument("--azimuth", type=float, default=180.0, help="180 = due south")
    parser.add_argument("--utc-offset", type=float, default=-5.0, help="local standard time offset")
    parser.add_argument("--ratios", action="store_true",
                        help="print monthly plane-of-array to horizontal ratios instead")
    args = parser.parse_args()

    if args.ratios:
        print(f"\nplane-of-array ({args.tilt:.0f} deg, azimuth {args.azimuth:.0f}) "
              f"vs horizontal, clear-sky beam + isotropic diffuse estimate:\n")
        for month in range(1, 13):
            profile = day_profile(datetime(2026, month, 15).date(), args.tilt,
                                  args.azimuth, args.utc_offset)
            flat = day_profile(datetime(2026, month, 15).date(), 0.0, 0.0, args.utc_offset)
            tilted = sum(p[1] for p in profile)
            horizontal = sum(p[1] for p in flat)
            ratio = tilted / horizontal if horizontal else 0.0
            print(f"  {datetime(2026, month, 15):%b}: {ratio:.2f}")
        print("\n(beam component only; the doc's table includes diffuse and ground reflection)")
        return

    month, day = (int(part) for part in args.date.split("-"))
    profile = day_profile(datetime(2026, month, day).date(), args.tilt, args.azimuth,
                          args.utc_offset)
    total = sum(p[1] for p in profile)
    peak = max(profile, key=lambda p: p[2])
    print(f"\n{datetime(2026, month, day):%b %d}: sun up {profile[0][0]:%H:%M}-{profile[-1][0]:%H:%M} "
          f"local standard time, peak elevation {peak[2]:.1f} deg at {peak[0]:%H:%M}")
    print(f"panel {args.tilt:.0f} deg tilt, azimuth {args.azimuth:.0f}\n")

    if not args.clear:
        print("pass --clear HH:MM-HH:MM (from an AR sun-path survey) to value a window.")
        return

    for window in args.clear:
        start, _, end = window.partition("-")
        captured = sum(p[1] for p in profile if start <= f"{p[0]:%H:%M}" <= end)
        share = 100 * captured / total if total else 0.0
        print(f"  clear {window}: {share:5.1f}% of the day's direct-beam energy "
              f"({100 - share:4.1f}% lost to the horizon)")


if __name__ == "__main__":
    main()
