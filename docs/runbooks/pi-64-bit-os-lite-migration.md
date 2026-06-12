# Raspberry Pi 64-Bit OS Lite Migration

Plan for rebuilding the supervisory Raspberry Pi on Raspberry Pi OS Lite
64-bit. This intentionally favors a small number of broad steps over minimum
downtime. The power system can be manually monitored and controlled while the
supervisor is offline.

## Current Status

Parked on 2026-06-12 while waiting for a second microSD card. Do not overwrite
the current working card.

Completed preparation:

- Migration backup created from the live Pi:
  `backups/pi-migration/offgrid-blueberry-20260612T120203Z.tar.gz` on the
  workstation, with a copy left on the Pi under `~/offgrid-backups/`.
- Backup contents were inspected and include `/etc/offgrid-power.env`, systemd
  units, udev rules, nginx config, SSH material, `/srv/telemetry`, and
  `/var/lib/offgrid`.
- Current Pi redeployed cleanly at Git commit `2456d2f`.
- Pi-side test suite passed: 171 tests.
- Live health check passed: supervisor, console, nginx, CAN watchdog timer, and
  metrics export timer active; `/healthz` and Kindle nginx path returned OK.
- Current boot card identified as a 64 GB-class Samsung card from 2018
  (`mmcblk0`, 59.6 GiB, product `GC2QT`), still serving as the rollback card.
- `/srv/telemetry` is on the external Samsung SSD 840 EVO 500 GB, not on the
  boot microSD.

Resume only after acquiring a new high-endurance microSD card. Preferred
target: 128 GB high-endurance microSDXC, UHS-I U1/U3, A1 or A2, from a reputable
vendor. The SanDisk 128 GB High Endurance card
`SDSQQNR-128G-GN6IA` is an acceptable target.

## Goal

Move the Pi from its current OS image to a clean 64-bit Lite install so modern
Python, Node, arm64 packages, and local developer tools are easier to run.

Secondary goal: verify whether Codex CLI is practical on the Pi after the base
supervisor is healthy. Codex CLI should not block restoration of telemetry.

## Migration Strategy

Use a clean image and restore, not an in-place architecture upgrade.

Reasons:

- A clean install is simpler to reason about than converting a live 32-bit
  system.
- Existing supervisor downtime is acceptable for hours or days.
- The current backup, restore, and install scripts are placeholders, so the
  safest preparation is to make the restore path explicit before wiping the
  card.
- The Pi is a Raspberry Pi 3 Model B v1.2 with 1 GB RAM, so 64-bit compatibility
  is useful but memory headroom remains tight.

## Preserve Before Reimage

Capture these from the running Pi before changing the SD card:

| Item | Source | Notes |
|---|---|---|
| Repo checkout | `~/power-system` or deployed project directory | Commit local changes first; avoid rsync-only drift |
| Environment file | `/etc/offgrid-power.env` | Contains local runtime settings and credentials |
| systemd rendered units | `/etc/systemd/system/offgrid-*.service`, `/etc/systemd/system/offgrid-*.timer` | Mostly reproducible from repo, but useful for diffing |
| udev rules | `/etc/udev/rules.d/90-offgrid-usb.rules` | Stable USB names and autosuspend policy |
| nginx site | `/etc/nginx/sites-available/offgrid-supervisor.conf` | Kindle-safe proxy path |
| Desktop console config | `~/.local/bin/open-offgrid-console`, `~/.config/autostart/offgrid-console.desktop` | Only relevant if the rebuilt image includes desktop packages later |
| Telemetry data | `/srv/telemetry`, `/var/lib/offgrid` | Preserve SQLite metrics, weather cache, and fallback store |
| SSH identity and access | `~/.ssh`, `/etc/ssh/sshd_config*` | Preserve access and GitHub deploy auth if used |
| Network config | NetworkManager/systemd-networkd/wpa config, hostname | Keep `blueberry.local` stable if consumers depend on it |
| Package inventory | `dpkg --get-selections`, manually installed packages | Reference only; do not blindly replay everything |
| Service state | `systemctl list-unit-files 'offgrid-*'`, enabled timers | Recreate intent, not necessarily exact files |

Keep at least one full SD-card image until the new system has survived a reboot
and a supervisor deploy.

## Target Baseline

- Raspberry Pi OS Lite 64-bit, current stable release.
- Hostname: `blueberry`.
- SSH enabled on first boot.
- Same primary user as the old install if practical.
- Git checkout at the normal project path, preferably `~/power-system`.
- Python virtual environment at `${PROJECT_DIR}/.venv`.
- Supervisor web/API on `127.0.0.1:8081`.
- nginx on ports `80` and `8080`.
- SocketCAN `can0` listen-only at 500 kbit/s.
- Stable serial symlinks from `config/udev/90-offgrid-usb.rules`.
- Telemetry mounted or restored at `/srv/telemetry`, with fallback at
  `/var/lib/offgrid`.

## High-Level Procedure

1. Leave the current working microSD card untouched as the physical rollback.
2. Flash the new microSD with Raspberry Pi OS Lite 64-bit and configure SSH,
   hostname, locale, timezone, and network.
3. Boot the Pi from the new card and confirm SSH access by hostname and IP.
4. Clone the repo:
   `git clone git@github.com:finitor/power-system.git ~/power-system`.
5. Copy the migration backup archive to the new Pi.
6. Restore local config and telemetry:
   `cd ~/power-system && scripts/restore-config.sh --apply ~/offgrid-blueberry-20260612T120203Z.tar.gz`.
7. Bootstrap packages, venv, config rendering, tests, restart, and deploy
   health checks: `scripts/install-pi.sh`.
8. Run `scripts/health-check.sh`.
9. Reboot once and repeat `scripts/health-check.sh`.
10. Only after telemetry is healthy, try installing and running Codex CLI
    locally. Treat performance or memory problems as a separate follow-up.

Preparation steps 1-3 from the original plan are already complete as of
2026-06-12; see Current Status above.

## Validation

Minimum successful migration checks:

```sh
uname -m
getconf LONG_BIT
hostname
systemctl is-active offgrid-supervisor offgrid-console nginx
systemctl is-active offgrid-can-watchdog.timer offgrid-metrics-export.timer
ip -details link show can0
ls -l /dev/epever-rs485 /dev/cubix-rs485 2>/dev/null || true
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS -A 'Kindle/3.0' http://127.0.0.1:8080/ >/dev/null
```

Functional checks:

- Dashboard loads through `http://blueberry.local/`.
- Kindle path on port `8080` returns HTTP 200 even if the supervisor is
  temporarily stopped.
- `/api/v1/snapshot` includes fresh battery CAN data.
- Classic telemetry and charge settings read correctly.
- EPEver telemetry reads through `/dev/epever-rs485` if the controller is
  connected.
- Metrics SQLite database is writing to `/srv/telemetry/data`.
- Fallback database path exists and is writable.
- Daily metrics export timer is enabled.
- CAN watchdog timer is enabled.

## Rollback

The simplest rollback is physical:

1. Shut down the rebuilt Pi.
2. Reinsert the old SD card or restored image.
3. Boot and confirm `blueberry.local` returns.
4. Run the normal health checks.

Do not erase the old SD card or backup image until the 64-bit install has
survived at least one deploy, one reboot, and a representative telemetry run.

## Prep Work To Do In Repo

- Done: `scripts/backup-config.sh` creates a timestamped migration backup.
- Done: `scripts/restore-config.sh` restores local config and telemetry with an
  explicit `--apply` guard.
- Done: `scripts/install-pi.sh` installs the minimal package and venv bootstrap
  for Raspberry Pi OS Lite 64-bit, then runs deploy.
- Done: `scripts/health-check.sh` runs the validation checks above.
- Add the chosen OS image and package baseline to `docs/maintenance.md` after
  the migration.

## Open Questions

- Which exact Raspberry Pi OS Lite 64-bit image should be pinned as the
  migration baseline?
- Which exact card was purchased and installed?
- Should `/srv/telemetry` stay on the SD card for the migration, or move to
  external storage during the rebuild? Current state is external SSD mounted at
  `/srv/telemetry`.
- Does the rebuilt Lite image need a local desktop console immediately, or can
  that wait until after telemetry is healthy?
- Does local Codex CLI need authenticated interactive use on the Pi, or only
  occasional CLI runs over SSH?
