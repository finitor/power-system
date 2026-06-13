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

Resume for a dry run with the salvaged 32 GB microSD, but keep the current
working card untouched. A 32 GB card is enough for a rehearsal because root
uses about 7.3 GB and telemetry writes live on the external SSD. Preferred
final target remains a 128 GB high-endurance microSDXC, UHS-I U1/U3, A1 or A2,
from a reputable vendor. The SanDisk 128 GB High Endurance card
`SDSQQNR-128G-GN6IA` is an acceptable final target.

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
  is useful but memory headroom needs care.

### Memory headroom: resolved by choosing Lite

Measured on the live 32-bit system (2026-06-12): of ~330 MB in use, the
desktop stack (`labwc`, `wf-panel-pi`, `pcmanfm`, the xdg portals,
`lxterminal`) accounts for roughly 150+ MB, while the console itself is just
tmux plus the Python `api_terminal_display` renderer — there is no browser in
the chain. Dropping the desktop on Lite more than offsets the ~25-30%
per-process overhead of 64-bit userland, so the rebuilt system should sit
*below* current memory usage. Headless 64-bit on 1 GB is the widely-reported
fine configuration; the "64-bit is painful on 1 GB" reports are almost
entirely desktop-plus-browser workloads, which this migration eliminates.
Remaining watch items: `pyarrow` import is ~100 MB in the export oneshot, and
any on-Pi DuckDB use should set `memory_limit`.

## Preserve Before Reimage

Capture these from the running Pi before changing the SD card:

| Item | Source | Notes |
|---|---|---|
| Repo checkout | `~/power-system` or deployed project directory | Commit local changes first; avoid rsync-only drift |
| Environment file | `/etc/offgrid-power.env` | Contains local runtime settings and credentials |
| Mount table | `/etc/fstab` | Preserve full file as reference; restore only `/srv/telemetry` line onto a new image |
| Host identity | `/etc/hostname`, `/etc/hosts`, cloud-init host template/config | Keep `blueberry` identity stable |
| systemd rendered units | `/etc/systemd/system/offgrid-*.service`, `/etc/systemd/system/offgrid-*.timer` | Mostly reproducible from repo, but useful for diffing |
| udev rules | `/etc/udev/rules.d/90-offgrid-usb.rules` | Stable USB names and autosuspend policy |
| nginx site | `/etc/nginx/sites-available/offgrid-supervisor.conf` | Kindle-safe proxy path |
| Desktop console config | `~/.local/bin/open-offgrid-console`, `~/.config/autostart/offgrid-console.desktop` | The console is terminal-only (tmux attach), so on Lite it runs on a bare tty — see "Local console without a desktop" below; the autostart `.desktop` file becomes obsolete |
| Telemetry data | `/srv/telemetry`, `/var/lib/offgrid` | Preserve SQLite metrics, weather cache, and fallback store |
| SSH identity and access | `~/.ssh`, `/etc/ssh/sshd_config*` | Preserve access and GitHub deploy auth if used |
| sudoers snippets | `/etc/sudoers.d` | Preserve as reference only; do not restore temporary privilege files automatically |
| Network config | NetworkManager/systemd-networkd/wpa config, hostname | Keep `blueberry.local` stable if consumers depend on it |
| Boot config | `/boot/firmware/config.txt`, `/boot/firmware/cmdline.txt` | Preserve as reference only; do not blindly restore root/cmdline from the old card |
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
2. Flash the dry-run microSD with Raspberry Pi OS Lite 64-bit and configure SSH,
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

## Watching The Bootstrap From The Mac

For long package installs, create a local tmux session that watches the new Pi
over SSH:

```sh
tmux new-session -d -s pi64-watch \
  "ssh -t -o UserKnownHostsFile=/tmp/offgrid-pi-known-hosts tvetter@192.168.0.200 'sudo tail -F /var/log/apt/term.log /var/log/dpkg.log'"

tmux split-window -t pi64-watch -v \
  "ssh -t -o UserKnownHostsFile=/tmp/offgrid-pi-known-hosts tvetter@192.168.0.200 'while true; do clear; date; echo; ps -eo pid,ppid,stat,pcpu,pmem,comm,args | egrep \"apt|dpkg|pip|python|unittest|deploy|install-pi|offgrid|nginx|systemctl\" | grep -v egrep; echo; systemctl --failed --no-pager; sleep 3; done'"

tmux attach -t pi64-watch
```

Useful keys:

```text
Ctrl-b then Up/Down   switch panes
Ctrl-b then d         detach and leave it running
```

Clean up later:

```sh
tmux kill-session -t pi64-watch
```

## Local Console Without a Desktop

The HDMI console does not need desktop packages: the display chain is purely
terminal-based (`offgrid-console.service` owns a tmux session;
`open-offgrid-console` just re-attaches to it in a loop). On Lite, run the
attach loop on the physical screen with getty autologin instead of a desktop
autostart entry:

```sh
sudo systemctl edit getty@tty1
```

```ini
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin <user> --noclear %I $TERM
```

and have the login shell exec `~/.local/bin/offgrid-tty-console` when running
on tty1. That script composes the screen: a left pane that re-attaches to the
service-owned display session (via `open-offgrid-console`), and a right pane
with a ready shell that survives service restarts and deploys, since it
belongs to the login's own tmux session. Same console, ~zero additional RAM.
This answers the former open question about whether the rebuilt image needs a
desktop: no.

**Validated 2026-06-12 on the current 32-bit image as a migration dry run:**
`systemctl set-default multi-user.target` plus this guard appended to
`~/.profile` (no `.bash_profile` exists, and creating one would stop bash
from reading `.profile`):

```sh
# Off-grid wall display: the tty1 autologin session becomes the console
# (attach loop re-attaches to the offgrid-console tmux session forever).
# Other ttys (Alt+F2...) stay normal shells.
if [ "$(tty)" = "/dev/tty1" ] && [ -z "${DISPLAY:-}" ] && [ -x "$HOME/.local/bin/offgrid-tty-console" ]; then
    exec "$HOME/.local/bin/offgrid-tty-console"
fi
```

After reboot: tty1 carries the attach loop with the tmux client on the
physical screen, all services healthy, and memory used dropped from ~300 MB
(desktop) to ~208 MB — confirming the headroom argument above with a measured
number. Rollback is `sudo systemctl set-default graphical.target` plus
removing the `.profile` guard.

**Console font (Greek coverage):** the kernel default console font is 8x16
with only 256 glyphs, so U+0394 (the cell-delta Δ in the display) renders as
a missing-glyph fallback. `/etc/default/console-setup` is set to
`CODESET="Uni2"` `FONTFACE="Terminus"` `FONTSIZE="8x16"` → `Uni2-Terminus16`,
which is the same 8x16 cell size (so the 1920x1080 / 8x16 = 240x67 geometry
and the pane layout are unchanged) but carries 512 glyphs including Greek.
Apply with `sudo setupcon --save`. This sets the *boot default*; the size is
then adjustable at runtime (below).

**Font size hotkeys:** `~/.local/bin/offgrid-console-font {up|down|reset}`
steps a preloaded Uni2-Terminus ladder (14 → 16 → 20x10 → 24x12 → 28x14 →
32x16) via `setfont`, persisting the index to
`~/.local/state/offgrid/console-font-index`. The composed console binds it to
`prefix +` / `prefix -` (repeatable) and `prefix 0` (reset), and re-applies
the saved size on startup. setfont reflows the tty (the framebuffer is fixed
at 1920x1080, so a bigger cell = fewer cols/rows); tmux resizes its panes and
the renderer adapts via its dynamic terminal-size read. Both the boot-default
config and the saved index are in the migration backup manifest.

## Post-Migration Follow-Ups

Once `uname -m` reports `aarch64` and telemetry is healthy:

- **Parquet serializer swap** (the one ADR 0003 amendment forced by armv7l):
  add `pyarrow` to the Pi venv and change the serialization in
  `build_export_batch` (`offgrid_power/r2_export.py`). The object layout is
  already Parquet-shaped (`metrics/{samples,events}/date=YYYY-MM-DD/`), so
  only the body format and object suffix change. Optionally convert the
  existing NDJSON archive objects with a one-shot workstation job.
- **DuckDB on-Pi becomes possible** (aarch64 wheel exists) for local rollups
  or serving history charts without the workstation; set `memory_limit` on a
  1 GB host.
- Codex CLI experiment, per the secondary goal.

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
- Done: backup/restore preserves `/etc/fstab`, boot config, hostname, hosts,
  cloud-init host template/config, sudoers snippets, SSH daemon local config,
  and NetworkManager/systemd-networkd/wpa_supplicant config as migration
  references. Restore appends only the `/srv/telemetry` fstab entry to a fresh
  image, avoiding stale root/boot PARTUUIDs; sudoers snippets are kept as
  reference only.
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
- Resolved 2026-06-12: the rebuilt Lite image does not need a desktop for the
  local console — the chain is terminal-only and runs on a tty (see "Local
  Console Without a Desktop"). This also resolves the memory-headroom concern
  in the rebuild's favor (see "Memory headroom: resolved by choosing Lite").
- Does local Codex CLI need authenticated interactive use on the Pi, or only
  occasional CLI runs over SSH?
