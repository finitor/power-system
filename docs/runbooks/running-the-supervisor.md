# Running the Supervisor

How the Raspberry Pi supervisor is launched — from "it normally runs itself" to
"rebuild it from scratch in a cold cabin." Written for someone who has not
touched the project in a while (or ever).

Throughout: the Pi is `blueberry.local`, you log in as **`<user>`** (the account
you created in Pi Imager), the repo lives at **`~/power-system`**, and the
service itself runs as the **`offgrid`** system user. Adjust if your install differs.

---

## 1. The short version (it runs itself)

The supervisor is a **systemd service** named `offgrid-supervisor`. It starts on
boot, restarts on failure, and you almost never launch it by hand. Day-to-day:

```sh
ssh <user>@blueberry.local

systemctl status offgrid-supervisor        # is it running?
journalctl -u offgrid-supervisor -f        # live logs (Ctrl-C to stop watching)
sudo systemctl restart offgrid-supervisor  # restart (e.g. after editing the env file)
sudo systemctl stop offgrid-supervisor     # stop (frees the RS485 adapters)
sudo systemctl start offgrid-supervisor

power-system/scripts/diag.sh               # one-call health digest
```

To ship a code or config change, **do not hand-edit anything on the Pi** — work
on the Mac, commit + push, then on the Pi:

```sh
cd power-system && bash scripts/deploy.sh
```

`deploy.sh` pulls git to truth, renders the config templates into their system
locations (including the systemd unit), reinstalls the package if dependencies
changed, restarts the services, and health-checks. It refuses to run with a
dirty Pi working tree.

### Marooned changes (the rule that keeps biting us)

The Pi checkout is **disposable** — git is the source of truth, and `deploy.sh`
reconciles to it. So any edit made directly on the Pi (a quick bench fix, a
`scp` of a single file) is *marooned* until it is committed and pushed from the
Mac. Marooned work has twice been found stranded here.

- **Detect it:** `scripts/diag.sh` reports a `git:` line — `clean`, or
  `DIRTY (N modified, M untracked)`. Since diag is the first move for anything,
  a dirty tree can't hide. (`git status` and `git -C ~/power-system status` work
  too.)
- **Why untracked files are the sneaky case:** a clean `git pull` ignores
  untracked files entirely, so a new file authored on the Pi (e.g. a new script)
  never blocks a deploy and never reaches git on its own. `deploy.sh` now prints
  a loud WARNING listing them; `diag.sh` counts them.
- **Reconcile before discarding** — the changes may exist *only* on the Pi:
  1. `git -C ~/power-system status` then `git diff` to see what's there.
  2. Copy anything real to the Mac (into the repo), commit, and push.
  3. Only then, on the Pi: `git checkout -- . && git clean -i` (review untracked
     interactively), then `bash scripts/deploy.sh`.

The standing rule: **bench iteration is fine, but every session ends by getting
the change into git** (commit + push from the Mac, then `deploy.sh`), leaving
the Pi tree clean.

---

## 2. First-time setup on a fresh Pi

Starting from a clean Raspberry Pi OS Lite 64-bit install with network access,
logged in as your normal user (not root):

```sh
git clone <repo-url> ~/power-system
cd ~/power-system
bash scripts/install-pi.sh
```

`install-pi.sh` does the whole bootstrap (it `sudo`s where needed — run it as
the normal user, it refuses to run as root):

- installs OS packages: `can-utils`, `nginx`, `sqlite3`, `tmux`, `python3-venv`,
  `build-essential`, `usbutils`, etc.;
- (optionally) installs Tailscale for remote access;
- creates `/srv/telemetry` + `/var/lib/offgrid` and the `offgrid` group;
- **builds the Python venv and installs the package** (see §3);
- runs `deploy.sh` to render configs and start services.

Then create the environment file (§4) and restart:

```sh
sudo cp ~/power-system/.env.example /etc/offgrid-power.env
sudo nano /etc/offgrid-power.env        # fill in the values you need
sudo chmod 600 /etc/offgrid-power.env
sudo systemctl restart offgrid-supervisor
```

---

## 3. The Python virtual environment

A **venv** is a self-contained Python environment so the project's pinned
dependencies don't collide with the system Python. This project's venv lives at
`~/power-system/.venv`.

`install-pi.sh` creates it; if you ever need to build it by hand:

```sh
cd ~/power-system
python3 -m venv .venv                                  # create it
.venv/bin/pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e .                             # install the project (editable)
.venv/bin/pip install -e ".[sensors]"                  # optional: DHT22/DS18B20 libs
```

- **Editable install** (`-e`) means the installed package points at the source
  tree, so a `git pull` of `.py` changes takes effect on the next restart
  without reinstalling. `deploy.sh` only reinstalls when `pyproject.toml` changed.
- Run anything with the venv's interpreter: `.venv/bin/python ...`, or use the
  installed console scripts directly: `.venv/bin/offgrid-supervisor`,
  `.venv/bin/offgrid-terminal-display`, `.venv/bin/offgrid-object-store-export`, etc.
- The Mac dev venv is the same idea (Python 3.12 there) and runs the full test
  suite: `.venv/bin/python -m pytest`.

---

## 4. The environment file: `/etc/offgrid-power.env`

The service loads this file (`EnvironmentFile=`). It holds site configuration
and secrets (B2 keys), which is why it lives outside git — `/.env.example` in
the repo is the template. Keep it root-owned and `600`. After editing, restart
the service for changes to take effect.

Common elements (see `.env.example` for the full list):

| Variable | What it is |
|---|---|
| `CLASSIC_HOST` / `CLASSIC_PORT` / `CLASSIC_DEVICE_ID` | MidNite Classic Modbus TCP target (default `192.168.0.10:502`, id 10) |
| `BATTERY_CAPACITY_AH` | Bank capacity for load/autonomy math (`200`) |
| `BATTERY_CAN_PROTOCOL` | BMS CAN dialect (`pylon`) |
| `MAGNUM_DEVICE` | Magnum RS485 serial device, e.g. `/dev/magnum-rs485`; **empty disables** the Magnum tap |
| `AMBIENT_SENSOR_ENABLED` / `AMBIENT_SENSOR_KIND` / `AMBIENT_DS18B20_DEVICE_ID` | Ambient temp sensor on GPIO |
| `TASMOTA_DEVICES` | Comma-separated `name=host` individual-load monitors. See [Adding a Tasmota Sonoff S31](add-tasmota-s31.md). |
| `WEATHER_ENABLED` / `WEATHER_LATITUDE` / `WEATHER_LONGITUDE` / `WEATHER_LABEL` | Open-Meteo weather panel (lat/lon are required if enabled) |
| `METRICS_DB_PATH` | SQLite telemetry store (`/srv/telemetry/data/metrics.sqlite`) |
| `B2_*` | Backblaze B2 store-and-forward export (bucket, keys, endpoint) |
| `CHARGE_ALLOC_*` / `CHARGE_CEILING_*` | Charge-allocator tuning (optional). See [charge-current-allocation.md](../charge-current-allocation.md) "Operator controls". **The allocator on/off toggle is the `--charge-allocation` flag in the systemd unit, NOT an env var — by design.** |

Note: several command-line arguments (below) default from these env vars, so the
systemd unit only needs to pass the ones it wants to set explicitly.

---

## 5. Command-line arguments

The service runs the `offgrid-supervisor` entry point through the thin wrapper
`scripts/supervisor-display.py`. The **canonical production invocation** is the
`ExecStart` in `config/systemd/offgrid-supervisor.service` (rendered onto the Pi
by `deploy.sh`):

```sh
.venv/bin/python scripts/supervisor-display.py \
  --classic-host ${CLASSIC_HOST} --epever-device /dev/epever-rs485 \
  --web-display --web-host 127.0.0.1 --web-port 8081 \
  --weather --weather-cache-path /srv/telemetry/data/weather-cache.json \
  --interval 5 --no-terminal-display \
  --metrics-db-path /srv/telemetry/data/metrics.sqlite \
  --metrics-db-mountpoint /srv/telemetry \
  --metrics-fallback-db-path /var/lib/offgrid/metrics-fallback.sqlite \
  --metrics-snapshot-interval 60 --charge-allocation
```

The arguments you'll touch most:

| Argument | Purpose |
|---|---|
| `--classic-host HOST` | MidNite Classic IP (defaults to `CLASSIC_HOST`) |
| `--epever-device PATH` | EPEver RS485 serial node (`/dev/epever-rs485`) |
| `--tasmota-device NAME=HOST` | Add a Tasmota energy monitor; repeat for multiple devices. Production normally uses `TASMOTA_DEVICES`. |
| `--interval N` | Seconds between poll cycles (`5`) |
| `--web-display` / `--web-host` / `--web-port` | Serve the JSON API + Kindle/web display (nginx fronts it) |
| `--weather` (+ `--weather-cache-path`) | Enable the weather panel |
| `--no-terminal-display` | Don't render the live TUI in this process (the service uses this; the wall console is a separate viewer) |
| `--metrics-db-path` / `--metrics-db-mountpoint` / `--metrics-fallback-db-path` / `--metrics-snapshot-interval` | Telemetry store + removable-SSD guard + SD fallback |
| `--charge-allocation` | **Live** charge-current allocation (writes controller limits). `--charge-allocation-dry-run` logs decisions only. Cannot run with the live charger taper. |
| `--once` | Single poll then exit (handy for a quick probe) |

Full list: `.venv/bin/offgrid-supervisor --help`.

---

## 6. Running it by hand (bench / debugging)

Only one process can own the RS485 adapters, so **stop the service first**:

```sh
sudo systemctl stop offgrid-supervisor

# Live terminal panel (omit --no-terminal-display to render the TUI):
.venv/bin/offgrid-supervisor \
  --classic-host 192.168.0.10 --epever-device /dev/epever-rs485 --interval 5

# Or a single one-shot read:
.venv/bin/offgrid-supervisor --classic-host 192.168.0.10 --epever-device /dev/epever-rs485 --once

# When done, hand control back to the service:
sudo systemctl start offgrid-supervisor
```

Before the supervisor starts, `offgrid-classic-clock-restore.service` gives
internet NTP 15 seconds to synchronize the Pi. If NTP is still unavailable, it
allows the Classic up to 120 seconds to boot and produce two plausible,
advancing RTC samples. A discontinuous clock change restarts confirmation so an
MNGP-to-main-board time copy is not mistaken for a stable RTC. NTP is rechecked
throughout the wait. The helper validates Classic time as `America/Toronto` and
advances the Pi clock when the Classic is ahead. It never steps the clock
backward, ignores the Classic's unreliable day-of-year register, and fails open
after a hard 150-second service timeout so an unavailable Classic cannot prevent
telemetry from starting. The helper runs as the unprivileged service account
with only `CAP_SYS_TIME`; the long-running supervisor receives no added
capability.

The rationale, source hierarchy, Modbus findings, race handling, and failure
semantics are documented in
[System timekeeping and Classic/MNGP recovery](../subsystems/timekeeping.md).

Diagnostic dry run (reads the Classic even though NTP is currently healthy):

```sh
sudo .venv/bin/python -m offgrid_power.cli.classic_clock_restore \
  --ignore-ntp --dry-run --ntp-wait-seconds 0 --classic-wait-seconds 0
```

`Ctrl-C` quits the manual run. Don't leave a hand-run supervisor going — it
won't survive a reboot and it blocks the service from owning the adapters.

---

## 7. Changing charge parameters (operator shortcuts)

Convenience wrappers in `scripts/`, run on the Pi from `~/power-system`. The
first two go through the supervisor's control API (no restart, no adapter
contention); the emergency pair stops the supervisor on purpose. Underneath they
use the same `POST /api/v1/control/...` endpoints documented in
[supervisor-api.md](../telemetry/supervisor-api.md) and the
`classic-charge-settings.py` / `epever-coil.py` tools.

| Script | What it does |
|---|---|
| `scripts/charge-classic-absorb.sh 55.0` | Set the Classic absorb voltage. **EQ auto-clamps up to absorb** (the controller enforces EQ ≥ absorb); **float is independent and untouched** — keep it below absorb yourself (`classic-charge-settings.py --float-voltage`). Guarded against the BMS CVL. |
| `scripts/charge-sync-epever.sh [offset]` | Copy the Classic's charge voltages to the EPEver (+optional volts offset). Classic absorb→EPEver boost, float→float, equalize→equalize; equalize is auto-clamped ≥ boost. CVL-guarded. |
| `scripts/charge-disable.sh` | **Emergency stop all charging.** Stops the supervisor (so the live allocator can't re-enable), then EPEver coil OFF (reliable) + Classic limit 0 A (best-effort). Telemetry is **off** while disabled. |
| `scripts/charge-enable.sh` | Resume: EPEver coil on + baseline Classic limit, then restart the supervisor (the allocator takes over if live). |

Why the emergency pair stops the supervisor: with `--charge-allocation` live, the
allocator reconciles the EPEver coil and rewrites the controller limits every
cycle, so a plain coil-off would be undone within seconds. Stopping the
supervisor freezes it. The BMS hard limits remain the real protection in any
case; for a guaranteed Classic stop, pull its PV breaker.

For anything beyond these (per-field voltages, current limits, charge timers),
use `scripts/classic-charge-settings.py --help`, `scripts/epever-coil.py`, and
the `CHARGE_ALLOC_*` / `CHARGE_CEILING_*` env knobs (see
[charge-current-allocation.md](../charge-current-allocation.md) "Operator
controls").

## 8. Where it serves, and how to look

- JSON API + display on `127.0.0.1:8081`; nginx fronts ports 80 and 8080 (the
  Kindle wall display is bookmarked to `:8080`).
- Quick check from the Pi: `curl -s localhost:8081/api/v1/snapshot | python3 -m json.tool`.
- The physical wall console is a separate tty1 tmux viewer; mirror it over ssh
  with `tmux -L offgrid-tty attach -t wall`.
- Health endpoint for monitors: `curl -s localhost:8081/api/v1/health`.

See also: [maintenance.md](../maintenance.md) (backup/restore),
[troubleshooting.md](../troubleshooting.md) (symptoms → checks), and
[commissioning.md](../commissioning.md) (first power-up).
