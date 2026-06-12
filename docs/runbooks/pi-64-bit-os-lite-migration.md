# Raspberry Pi 64-Bit OS Lite Migration

Plan for rebuilding the supervisory Raspberry Pi on Raspberry Pi OS Lite
64-bit. This intentionally favors a small number of broad steps over minimum
downtime. The power system can be manually monitored and controlled while the
supervisor is offline.

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

1. Commit and push any workstation changes that should exist on the Pi.
2. Make the backup script real enough to create one timestamped migration
   bundle from the live Pi.
3. Run the backup and copy the bundle off the Pi.
4. Optionally image the old SD card as a full rollback artifact.
5. Flash Raspberry Pi OS Lite 64-bit and configure SSH, hostname, locale,
   timezone, and network.
6. Boot the Pi and confirm SSH access by hostname and IP.
7. Install the small OS package set needed by the supervisor:
   `git`, `python3`, `python3-venv`, `python3-pip`, `can-utils`, `tmux`,
   `nginx`, `curl`, `rsync`, and build tools needed by Python wheels.
8. Clone or restore the repo checkout.
9. Create `.venv`, install the project editable, and install sensor extras if
   the GPIO sensor path is active.
10. Restore `/etc/offgrid-power.env`, telemetry data, udev rules, nginx config,
    and any SSH/Git credentials needed for deploys.
11. Run `scripts/deploy.sh` from the Pi checkout to render service templates,
    install config, run tests, restart services, and health-check the result.
12. Reboot once and repeat the health checks.
13. Only after telemetry is healthy, try installing and running Codex CLI
    locally. Treat performance or memory problems as a separate follow-up.

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

- Replace `scripts/backup-config.sh` with a real migration backup command.
- Replace `scripts/restore-config.sh` with a conservative restore helper, or
  document manual restore commands if a scripted restore is too risky.
- Replace `scripts/install-pi.sh` with the minimal package and venv bootstrap
  for Raspberry Pi OS Lite 64-bit.
- Replace `scripts/health-check.sh` with the validation commands above.
- Add the chosen OS image and package baseline to `docs/maintenance.md` after
  the migration.

## Open Questions

- Which exact Raspberry Pi OS Lite 64-bit image should be pinned as the
  migration baseline?
- Is the current Pi SD card being replaced, or should the old card be preserved
  and a new card flashed?
- Should `/srv/telemetry` stay on the SD card for the migration, or move to
  external storage during the rebuild?
- Does the rebuilt Lite image need a local desktop console immediately, or can
  that wait until after telemetry is healthy?
- Does local Codex CLI need authenticated interactive use on the Pi, or only
  occasional CLI runs over SSH?
