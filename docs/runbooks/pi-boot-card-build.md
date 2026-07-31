# Building a New Pi Boot Card

Standard procedure for building a new boot microSD for the supervisory
Raspberry Pi. Raspberry Pi OS Lite 64-bit is the established baseline for this
system. The procedure intentionally favors a small number of broad steps over
minimum downtime. The power system can be manually monitored and controlled
while the supervisor is offline.

## Build History

**2026-06-22 — second build; Samsung EVO Select 64 GB (product `GC2QT`).**
Card flashed and in service as `blueberry`. The SP 3D NAND 32 GB card is the
physical rollback.

Card speeds (measured): SP 3D NAND 32 GB — 23.8 MB/s read; Samsung EVO Select
64 GB — 8 MB/s write / 18.2 MB/s read. For future builds prefer a
high-endurance U3/A2 card; the EVO Select is a mainstream consumer card and
notably slower on writes.

Friction captured during this build and fixed in the same session:

- `backup-config.sh` must run on the Pi via SSH, not locally on the Mac.
  Running it locally produces a useless archive (wrong hostname, no Pi config).
  Claude runs this step directly when in session.
- After taking the Pi backup, SCP it to the Mac before swapping the card.
  The old card's filesystem is gone once you boot the new one. The runbook
  was missing this step (now step 1b below).
- `git` is not installed on a fresh Raspberry Pi OS Lite image.
  `install-pi.sh` now lists it in the apt block (it was already there; no
  change needed — but worth noting so the operator doesn't hand-install it).
- `install-pi.sh` called `usermod -aG offgrid` before `deploy.sh` created
  the `offgrid` group. Fixed: `usermod` now runs after `deploy.sh`.
- `restore-config.sh` restored `~/.local/bin/` files via `sudo cp -a`,
  leaving the parent directory owned by root. `deploy.sh` then couldn't
  overwrite the files. Fixed: `restore-config.sh` now `chown`s `~/.local`
  back to the operator user after restoring home files.
- No `~/.ssh/config` existed on the old Pi, so `git pull` failed after
  the clone (git didn't know to use `blueberry_deploy` for GitHub). Fixed:
  `restore-config.sh` now writes `~/.ssh/config` if the deploy key is
  present but no config file exists.
- Decline **Raspberry Pi Connect** in the Imager setup flow — it is not
  needed and adds unnecessary background services.

**2026-06-13 — first 64-bit build.** Dry run on the salvaged 32 GB microSD;
card now in service as `blueberry`. The former 64 GB Samsung card is the
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
telemetry writes live on the external SSD. The original 64 GB Samsung card
(`mmcblk0`, 59.6 GiB, product `GC2QT`) is the current target for the next
build. A 128 GB high-endurance microSDXC (UHS-I U1/U3, A1 or A2) such as the
SanDisk `SDSQQNR-128G-GN6IA` is an acceptable future upgrade.

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

Build a clean boot card running Raspberry Pi OS Lite 64-bit with the full
supervisor stack deployed and telemetry healthy. Modern Python, Node, arm64
packages, and local developer tools are all available on this baseline.

## Approach

Use a clean image and restore, not an in-place upgrade.

Reasons:

- A clean install is simpler to reason about.
- Supervisor downtime is acceptable for hours or days.
- The backup, restore, and install scripts make the restore path explicit and
  repeatable.
- The Pi is a Raspberry Pi 3 Model B v1.2 with 1 GB RAM; memory headroom needs
  care (see below).

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
| Runtime state | `/var/lib/offgrid` | Charge-allocator runtime state + SD fallback store (small). `/srv/telemetry` is deliberately **not** backed up — it is operational data on the external SSD (a rebuild remounts it, never reimages it) and is exported to B2; only its fstab mount line is preserved |
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
- Bubblewrap sandbox binary at `/usr/bin/bwrap`.
- Official Codex CLI at `~/.local/bin/codex`.
- Supervisor web/API on `127.0.0.1:8081`.
- nginx on ports `80` and `8080`.
- SocketCAN `can0` listen-only at 500 kbit/s.
- Stable serial symlinks from `config/udev/90-offgrid-usb.rules`.
- Telemetry mounted or restored at `/srv/telemetry`, with fallback at
  `/var/lib/offgrid`.

## High-Level Procedure

1. Take a fresh backup from the running Pi before touching anything.
   Claude will run this directly when in session:
   `ssh <user>@blueberry.local 'cd power-system && bash scripts/backup-config.sh'`
   Note the archive path printed at the end (e.g.
   `/home/<user>/offgrid-backups/offgrid-blueberry-<stamp>.tar.gz`).

1b. SCP the backup archive from the Pi to the Mac **before** swapping the
    card — the old card's filesystem is gone once the new card boots:
    `scp <user>@blueberry.local:<archive-path> ~/offgrid-backups/`

2. Leave the current working microSD card as the physical rollback; do not
   erase it until the new card has passed all health checks.

3. Flash the target microSD card with Raspberry Pi OS Lite 64-bit. Use
   Raspberry Pi Imager; set the hostname (`blueberry`), your SSH public key,
   locale, timezone, and network in its advanced options before writing.
   **Decline Raspberry Pi Connect** when prompted — it is not needed.

   The SSH public key to paste is the Mac's `~/.ssh/id_ed25519.pub` (run
   `cat ~/.ssh/id_ed25519.pub` to get it). Imager injects it into the Pi's
   `authorized_keys` on first boot.

   Also confirm the Mac's `~/.ssh/config` loads the key persistently:
   ```sh
   grep -q "UseKeychain" ~/.ssh/config 2>/dev/null || cat >> ~/.ssh/config <<'EOF'

   Host *
       AddKeysToAgent yes
       UseKeychain yes
       IdentityFile ~/.ssh/id_ed25519
   EOF
   ssh-add --apple-use-keychain ~/.ssh/id_ed25519
   ```

4. Boot the Pi from the new card and confirm SSH access: `ssh <user>@blueberry.local`.
   `blueberry.local` may not resolve immediately — use the IP address from
   `arp -a | grep b8:27` on the Mac. Once connected, clear the stale host key:
   `ssh-keygen -R blueberry.local`
   (Claude handles this when in session; the new card generates fresh SSH host
   keys so the Mac's `known_hosts` will otherwise block `blueberry.local`.)

5. Copy the backup archive from the Mac to the new Pi:
   `scp ~/offgrid-backups/<archive-filename>.tar.gz <user>@<new-pi-ip>:~`
   Use the IP address until mDNS is up (`blueberry.local` may not resolve yet).

6. Extract SSH keys from the archive so the git clone can authenticate.
   The restore overwrites `~/.ssh/` from the bundle, which would clobber the
   `authorized_keys` Imager just injected — save it first, then merge it back:
   ```sh
   ARCHIVE=~/<archive-filename>.tar.gz
   BUNDLE="$(tar -tzf "${ARCHIVE}" | head -1 | cut -d/ -f1)"
   IMAGER_AUTHKEYS="$(cat ~/.ssh/authorized_keys 2>/dev/null)"
   tar -xzf "${ARCHIVE}" --strip-components=3 -C "${HOME}" \
       "${BUNDLE}/home/offgrid-user/.ssh"
   chmod 700 "${HOME}/.ssh" && find "${HOME}/.ssh" -type f -exec chmod 600 {} \;
   # Re-merge the Imager-injected key if the restore dropped it
   if [ -n "${IMAGER_AUTHKEYS}" ]; then
       grep -qF "${IMAGER_AUTHKEYS}" ~/.ssh/authorized_keys 2>/dev/null \
           || echo "${IMAGER_AUTHKEYS}" >> ~/.ssh/authorized_keys
   fi
   ```

7. Clone the repo:
   `git clone git@github.com:finitor/power-system.git ~/power-system`

8. Restore local config and telemetry:
   `cd ~/power-system && scripts/restore-config.sh --apply <archive>`

9. Bootstrap packages, venv, config rendering, tests, and deploy:
   `scripts/install-pi.sh`

9a. Reinstall the Codex CLI and its Bubblewrap sandbox dependency. These are
    binaries, not configuration to copy from the old card: install Bubblewrap
    from Debian and use OpenAI's [standalone installer](https://learn.chatgpt.com/docs/codex/cli)
    so it selects the current Linux ARM64 Codex build:
    ```sh
    sudo apt-get install -y bubblewrap
    curl -fsSL https://chatgpt.com/codex/install.sh | sh
    ```
    The installer puts Codex under `~/.codex/packages/standalone/` and exposes
    it as `~/.local/bin/codex`. Authentication is separate; if the rebuilt Pi
    is signed out, run `codex login --device-auth` and complete the browser
    flow. Do not copy Codex credentials from another machine.

10. Start a new SSH session — `install-pi.sh` adds `$USER` to the `offgrid`
    group, which only takes effect after re-login.

11. Run `scripts/health-check.sh`.

12. Reboot once and repeat `scripts/health-check.sh`.

## Validation

Minimum checks:

```sh
uname -m
getconf LONG_BIT
hostname
command -v bwrap && bwrap --version
bwrap --ro-bind / / --proc /proc --dev /dev /bin/true
bash -lc 'command -v codex && codex --version && codex login status'
systemctl is-active offgrid-supervisor offgrid-console nginx
systemctl is-active offgrid-can-watchdog.timer offgrid-supervisor-watchdog.timer offgrid-metrics-export.timer
# Hardware watchdog armed (RuntimeWatchdogUSec non-zero) and journald persistent
systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec
journalctl --header | grep -m1 "File path: /var/log/journal" && echo "journald: persistent"
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
- Supervisor blackout-watchdog timer is enabled and **in dry-run** — a fresh card
  correctly comes up unarmed (`SUPERVISOR_WATCHDOG_ARMED` unset). Only arm it
  (`SUPERVISOR_WATCHDOG_ARMED=1` in `/etc/offgrid-power.env`) after a burn-in
  shows no false "would reboot" entries. See `docs/runbooks/healthchecks-escalation.md`.
- Hardware watchdog armed (`RuntimeWatchdogUSec` non-zero) and journald persistent
  (`/var/log/journal` populated, not volatile in RAM). Both are applied by deploy.

## Watching The Bootstrap From The Mac

For long package installs, create a local tmux session that watches the new Pi
over SSH:

```sh
tmux new-session -d -s pi-bootstrap-watch \
  "ssh -t -o UserKnownHostsFile=/tmp/offgrid-pi-known-hosts <user>@192.168.0.200 'sudo tail -F /var/log/apt/term.log /var/log/dpkg.log'"

tmux split-window -t pi-bootstrap-watch -v \
  "ssh -t -o UserKnownHostsFile=/tmp/offgrid-pi-known-hosts <user>@192.168.0.200 'while true; do clear; date; echo; ps -eo pid,ppid,stat,pcpu,pmem,comm,args | egrep \"apt|dpkg|pip|python|unittest|deploy|install-pi|offgrid|nginx|systemctl\" | grep -v egrep; echo; systemctl --failed --no-pager; sleep 3; done'"

tmux attach -t pi-bootstrap-watch
```

Useful keys:

```text
Ctrl-b then Up/Down   switch panes
Ctrl-b then d         detach and leave it running
```

Clean up later:

```sh
tmux kill-session -t pi-bootstrap-watch
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
config and the saved index are in the backup manifest.

**Tailscale:** `scripts/install-pi.sh` installs Tailscale from the official
Tailscale apt repository when it is missing, then enables `tailscaled`.
`scripts/backup-config.sh` includes `/var/lib/tailscale` and
`/etc/default/tailscaled`, and `scripts/restore-config.sh` restores them so a
future card swap can keep the same machine identity. If no identity was
restored, run `sudo tailscale up --hostname=blueberry`, approve the auth URL,
and rename/remove any stale duplicate device in the Tailscale admin console.

## Post-Build Tasks

Once `uname -m` reports `aarch64` and telemetry is healthy:

- ~~**Parquet serializer swap**~~ **DONE 2026-06-16:** `pyarrow==24.0.0`
  added to the package deps (installs into the Pi venv on deploy);
  `build_export_batch` (`offgrid_power/object_store_export.py`) writes snappy Parquet
  (`.parquet` objects) under the unchanged hive layout. The old NDJSON
  archive objects were deleted rather than converted — operator chose a
  clean start, so the B2 bucket is now uniformly Parquet.
- **DuckDB on-Pi becomes possible** (aarch64 wheel exists) for local rollups
  or serving history charts without the workstation; set `memory_limit` on a
  1 GB host.
- ~~**Codex CLI experiment**~~ **DONE 2026-07-31:** the official standalone
  installer selected the Linux ARM64 build and installed it under
  `~/.codex/packages/standalone/`, with `~/.local/bin/codex` as the command.
  Debian's `bubblewrap` package supplies `/usr/bin/bwrap`, which Codex needs
  for its Linux sandbox. Both binaries are recreated by step 9a rather than
  archived from the old card; Codex authentication is handled separately.
- ~~**Claude CLI setup**~~ **DONE 2026-06-22:** `nodejs` and `npm` added to
  the `install-pi.sh` apt package list; `@anthropic-ai/claude-code` installed
  globally via `sudo npm install -g` after deploy. Auth state lives in
  `~/.claude.json` — now included in the backup/restore manifest so it
  survives future card swaps without re-authenticating. If no auth was
  restored, run `claude` on the Pi and follow the browser flow once.

## Rollback

The simplest rollback is physical:

1. Shut down the rebuilt Pi.
2. Reinsert the old SD card or restored image.
3. Boot and confirm `blueberry.local` returns.
4. Run the normal health checks.

Do not erase the old SD card or backup image until the new card has survived
at least one deploy, one reboot, and a representative telemetry run.

## Keeping The Rollback Card Current

The rollback card does **not** need to capture incremental changes like the watchdog
or journald work — and trying to keep a hot clone byte-current is the wrong model. The
card is *derivable*, so "current" means *rebuildable*, from two sources:

1. **git is the source of truth for everything that provisions the system.** `config/`
   + `deploy.sh` + `install-pi.sh` render and apply all units, drop-ins, udev rules,
   nginx, and ownership. A rebuild — or simply `git pull && bash scripts/deploy.sh` on
   any card — reproduces the live config exactly. This session's work (journald
   persistence, the hardware-watchdog drop-in, the blackout-watchdog) all landed in
   git/deploy precisely so a rebuilt card inherits it with no manual steps.
2. **The backup archive captures the non-git state** a rebuild can't regenerate:
   `/etc/offgrid-power.env` (secrets + flags like `SUPERVISOR_WATCHDOG_ARMED` and any
   `HC_*` URLs), host/SSH/Tailscale identity, and the `/srv/telemetry` fstab line.

So the discipline that keeps the backup current is not "re-clone the card" but:

- **Never let provisioning live only on the live card.** Every operational change goes
  into git (a unit, a drop-in, a deploy step) *or* the backup archive. A `sudo` edit
  that exists nowhere else is the drift that strands work — exactly what nearly happened
  with the journald/watchdog changes before they were codified this session.
- **Refresh the backup archive (`scripts/backup-config.sh`) after any non-git change**
  — e.g. after arming the watchdog or adding Healthchecks URLs — and keep the latest
  archive on the Mac (step 1b).
- **Optionally keep a hot spare**: boot the rollback card after a significant deploy and
  run `git pull && bash scripts/deploy.sh` so it stays bootable-and-current. Otherwise
  rely on the build procedure to produce a current card from latest git + latest archive
  when needed.

## Script and Repo Support

- `scripts/backup-config.sh` — creates a timestamped config backup archive.
- `scripts/restore-config.sh` — restores local config and telemetry with an
  explicit `--apply` guard. Preserves `/etc/fstab`, boot config, hostname,
  hosts, cloud-init, sudoers, SSH daemon config, NetworkManager/wpa config,
  and Tailscale identity. Appends only the `/srv/telemetry` fstab entry to a
  fresh image; boot PARTUUIDs are left as reference only.
- `scripts/install-pi.sh` — installs OS packages (including `sqlite3`), Python
  venv, Tailscale, and runs deploy.
- `scripts/health-check.sh` — runs the validation checks above.

## Card Selection

For indefinite operation until a high-endurance card is acquired: prefer the
card currently in service over swapping back to the rollback. Both cards in
rotation as of 2026-06-22 are mainstream consumer cards — neither is ideal for
24/7 embedded duty — but the telemetry workload (writes) lives on the external
SSD, so the boot card sees mostly reads. Swap only if the in-service card shows
filesystem errors or read errors in `dmesg`. When acquiring a replacement,
prefer a high-endurance UHS-I U3/A2 card such as the SanDisk
`SDSQQNR-128G-GN6IA`; 64 GB is sufficient given telemetry is off-card.

Benchmark both cards at each build with:
`sudo dd if=/dev/mmcblk0 of=/dev/null bs=1M count=64`
and record the read speed in the build history entry.

| Date | Card | Read speed | Notes |
|---|---|---|---|
| 2026-06-13 | SP 3D NAND 32 GB | 23.8 MB/s | First 64-bit build; now rollback |
| 2026-06-22 | Samsung EVO Select 64 GB (`GC2QT`) | 18.2 MB/s | 8 MB/s write; in service |

## Open Questions

- Which exact Raspberry Pi OS Lite 64-bit image should be pinned as the
  build baseline? Record the image version in `docs/maintenance.md` after the
  next build.
