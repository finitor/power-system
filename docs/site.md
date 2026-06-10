# Site

## Location

The installation is near Wawa, Ontario, Canada.

Exact coordinates are deliberately not recorded in this repo; they live in
the deployment environment file (`WEATHER_LATITUDE` / `WEATHER_LONGITUDE`
in `/etc/offgrid-power.env`; see `.env.example` at the repo root for the
full variable list). The approximate values below are sufficient for the
climate and solar discussions in these docs.

| Field | Value |
|---|---:|
| Latitude | ~47.9 N |
| Longitude | ~84.8 W |
| Time zone | America/Toronto |
| Standard time offset | UTC-05:00 |
| Daylight time offset | UTC-04:00 |

## Solar Noon

Solar noon is when the sun crosses the local meridian. It is not usually 12:00 on the clock because clock time is based on time-zone meridians, while the site is at its own longitude. The equation of time also shifts apparent solar time through the year.

For Wawa, use:

```text
solar noon clock minutes =
  720
  - 4 * longitude_degrees
  - equation_of_time_minutes
  + time_zone_offset_minutes
```

where:

- `longitude_degrees` is negative west of Greenwich. For this site, use approximately `-84.8`.
- `time_zone_offset_minutes` is `-300` for Eastern Standard Time or `-240` for Eastern Daylight Time.
- `equation_of_time_minutes` is the date-dependent correction from a solar-position calculator or ephemeris.
- The result is minutes after local midnight.

Using the approximate site longitude, the longitude-only correction is about:

```text
4 * (84.8 - 75.00) = 39.2 minutes
```

So before the equation-of-time correction:

| Clock regime | Approximate solar noon before equation-of-time correction |
|---|---:|
| Eastern Standard Time | 12:39 |
| Eastern Daylight Time | 13:39 |

Then subtract the equation of time for the date using the formula above. For example, if the equation of time is `+2.5 minutes`, daylight-time solar noon is about:

```text
13:39 - 2.5 minutes = 13:36:30
```

For operational use, prefer a library or published solar-position calculator that accepts latitude, longitude, date, and time zone — fed with the precise coordinates from the environment file. The hand calculation is useful for sanity-checking expected production timing and understanding why maximum PV output may occur well after civil noon.
