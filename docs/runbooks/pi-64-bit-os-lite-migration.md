# Raspberry Pi 64-Bit OS Lite Migration

Plan for rebuilding the supervisory Raspberry Pi on Raspberry Pi OS Lite
64-bit. This intentionally favors a small number of broad steps over minimum
downtime. The power system can be manually monitored and controlled while the
supervisor is offline.

## Current Status

Dry run completed on 2026-06-13 using the salvaged 32 GB microSD. The card is
currently in service as `blueberry`; the former 64 GB Samsung card remains the
physical rollback.

Preparation completed before the dry run:

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

A 32 GB card was enough for the rehearsal because root used about 7.3 GB and
telemetry writes live on the external SSD. Preferred final target remains a
128 GB high-endurance microSDXC, UHS-I U1/U3, A1 or A2, from a reputable
vendor. The SanDisk 128 GB High Endurance card `SDSQQNR-128G-GN6IA` is an
acceptable final target.

Validated after the 64-bit rebuild:

- Raspberry Pi OS Lite 64-bit, Debian GNU/Linux 13/trixie, `aarch64`.
- Repo deployed at Git commit `4db22d5`.
- Reboot survived; `/srv/telemetry` remounted from the external SSD.
- `offgrid-supervisor`, `offgrid-console`, `nginx`,
  `offgrid-can-watchdog.timer`, and `offgrid-metrics-export.timer` are enabled
  and active.
- Supervisor `/healthz` returns `ok`; nginx Kindle path returns OK.
- CAN is up listen-only at 500 kbit/s.
- Classic, battery CAN, and EPEver telemetry are healthy.
- Operational sudoers drop-in is installed by deploy so console font hotkeys,
  backups, and deploys can use non-interactive sudo.

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
| sudoers snippets | `/etc/sudoers.d` | Preserve as reference only; deploy recreates the known `020_offgrid_operator` NOPASSWD policy |
| Network config | NetworkManager/systemd-networkd/wpa config, hostname | Keep `blueberry.local` stable if consumers depend on it |
| Tailscale identity | `/var/lib/tailscale`, `/etc/default/tailscaled` | Preserve if available; otherwise reinstall and re-auth after rebuild |
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
ls -l /dev/epever-rs485 /dev/cubix-rs485 /dev/serial/by-path/*1.3.3* 2>/dev/null || true
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS -A 'Kindle/3.0' http://127.0.0.1:8080/ >/dev/null
```

Functional checks:

- Dashboard loads through `http://blueberry.local/`.
- Kindle path on port `8080` returns HTTP 200 even if the supervisor is
  temporarily stopped.
- `/api/v1/snapshot` includes fresh battery CAN data.
- Classic telemetry and charge settings read correctly.
- EPEver telemetry reads through the pinned by-path adapter
  `/dev/serial/by-path/platform-3f980000.usb-usb-0:1.3.3:1.0-port0` if the
  controller is connected. The `/dev/epever-rs485` symlink is ambiguous when
  the second serial-less CH340 tap is present.
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
autostart entry. `scripts/deploy.sh` installs the tracked `getty@tty1`
override and appends the `.profile` guard idempotently. If repairing by hand,
the equivalent override is:

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

The outer tty tmux layout defaults the left display pane to 96 columns. That is
intentionally narrower than the renderer's 120-column cap so a 192-column
console leaves a useful shell pane. Override with `OFFGRID_TTY_DISPLAY_COLS`
only if the physical display/font geometry changes enough to justify it.

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

**Console font:** the kernel default console font is 8x16 with only 256
glyphs, so the display's non-ASCII glyphs — the cell-delta U+0394 (Δ) and the
trend arrows U+2191/U+2193 (↑↓) — render as missing-glyph fallbacks.
`/etc/default/console-setup` is set to `CODESET="Uni2"`
`FONTFACE="TerminusBold"` `FONTSIZE="10x20"` → `Uni2-TerminusBold20x10`: a
Uni2 face (512 glyphs covering Greek + arrows), Bold for heavier/less-spindly
strokes than plain Terminus, at the operator's chosen default of one step up
from the 8x16 stock size (10x20 cell → 1920x1080 / 10x20 = 192x54). VGA/Fixed
are heavier still but exist only at 8x16, so they would forfeit the size
ladder below. Apply with `sudo setupcon --save`. This sets the *boot default*;
the size is then adjustable at runtime (below).

**Font size hotkeys:** `~/.local/bin/offgrid-console-font {up|down|reset}`
steps a preloaded Uni2-TerminusBold ladder (14 → 16 → 20x10 → 24x12 → 28x14 →
32x16; default/reset = index 2, 20x10) via `setfont`, persisting the index to
`~/.local/state/offgrid/console-font-index`. The composed console binds it to
the root-table keys **F7 (smaller) / F8 (bigger) / F6 (reset)** — no prefix,
because the nested display tmux makes the prefix unreliable from the physical
keyboard; `prefix +`/`-`/`0` are also bound. On an Apple keyboard the F-keys
need `Fn` (the top row defaults to media keys via `hid_apple fnmode`). The
renderer's footer shows a `Font  ↓F7  ↑F8` reminder. setfont reflows the tty
(the framebuffer is fixed at 1920x1080, so a bigger cell = fewer cols/rows);
tmux resizes its panes and the renderer adapts via its dynamic terminal-size
read. The console re-applies the saved size on startup. Both the boot-default
config and the saved index are in the migration backup manifest.

**Tailscale:** `scripts/install-pi.sh` installs Tailscale from the official
Tailscale apt repository when it is missing, then enables `tailscaled`.
`scripts/backup-config.sh` includes `/var/lib/tailscale` and
`/etc/default/tailscaled`, and `scripts/restore-config.sh` restores them so a
future card swap can keep the same machine identity. If no identity was
restored, run `sudo tailscale up --hostname=blueberry`, approve the auth URL,
and rename/remove any stale duplicate device in the Tailscale admin console.

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
  NetworkManager/systemd-networkd/wpa_supplicant config, and Tailscale
  identity as migration references. Restore appends only the `/srv/telemetry`
  fstab entry to a fresh image, avoiding stale root/boot PARTUUIDs; arbitrary
  sudoers snippets are kept as reference only, and deploy recreates the known
  operator NOPASSWD drop-in from tracked config.
- Done: `scripts/install-pi.sh` installs the minimal package and venv bootstrap
  for Raspberry Pi OS Lite 64-bit, installs Tailscale if missing, then runs
  deploy.
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
