"""Site coordinates for the modeling scripts.

Exact coordinates are deliberately **not** recorded in this repo (see
docs/site.md); they live in the deployment environment file as
WEATHER_LATITUDE / WEATHER_LONGITUDE, the same variables the supervisor's
weather reader uses.

Resolution order:

1. `WEATHER_LATITUDE` / `WEATHER_LONGITUDE` in the environment;
2. those keys in the env file (`OFFGRID_ENV_FILE`, default
   `/etc/offgrid-power.env`) if it is readable -- so scripts work on the Pi
   without the caller having to source it;
3. the published approximate values from docs/site.md.

The fallback costs the model nothing at the selected historical-weather grid:
the approximate and exact coordinates resolve to the same cell. Verified
2026-07-30 -- every scenario figure is identical either way, and the solstice
beam-window fraction moves 96.3% -> 96.2%. Precision matters for solar-geometry
timing (solar noon shifts ~4 minutes per degree of longitude), not for this
irradiance lookup.

Note that `/etc/offgrid-power.env` is root-only (0600, it holds secrets), so
step 2 does not apply when running as the telemetry owner:

    sudo -u offgrid python3 scripts/calibrate_pv.py ...

takes the approximation, which is the intended normal path -- these scripts
have no business reading a secrets file. Export the variables explicitly if a
run genuinely needs exact coordinates.
"""
from __future__ import annotations

import os

# docs/site.md publishes these; same selected historical-weather cell as exact.
APPROXIMATE_LAT, APPROXIMATE_LON = 47.9, -84.8
DEFAULT_ENV_FILE = "/etc/offgrid-power.env"


def _from_env_file(path):
    values = {}
    try:
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def site_coordinates(verbose=True):
    """Return (latitude, longitude, source) for the installation."""
    lat, lon = os.getenv("WEATHER_LATITUDE"), os.getenv("WEATHER_LONGITUDE")
    source = "environment"

    if not (lat and lon):
        env_file = _from_env_file(os.getenv("OFFGRID_ENV_FILE", DEFAULT_ENV_FILE))
        lat = env_file.get("WEATHER_LATITUDE")
        lon = env_file.get("WEATHER_LONGITUDE")
        source = os.getenv("OFFGRID_ENV_FILE", DEFAULT_ENV_FILE)

    if lat and lon:
        try:
            return float(lat), float(lon), source
        except ValueError:
            pass

    if verbose:
        print(f"note: using approximate site coordinates from docs/site.md "
              f"({APPROXIMATE_LAT}, {APPROXIMATE_LON}); set WEATHER_LATITUDE / "
              f"WEATHER_LONGITUDE for the exact location.")
    return APPROXIMATE_LAT, APPROXIMATE_LON, "docs/site.md approximation"
