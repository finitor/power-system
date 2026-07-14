# Display Services

How supervisor telemetry reaches the three display surfaces, and how the
chain behaves across restarts. The restart behavior is engineered; both
failure modes below were hit in production on 2026-06-09 while adding the
Magnum inverter device and are guarded now.

## Service Chain

```text
offgrid-supervisor.service  (python, web/API server on 127.0.0.1:8081)
  ├── nginx  (ports 80 and 8080 → proxy to 8081, retry page on 502/503/504)
  │     └── Kindle wall display  (bookmarked to :8080, 60s meta refresh)
  ├── offgrid-console.service  (BindsTo=offgrid-supervisor)
  │     └── tmux session "offgrid-console"
  │           └── api_terminal_display  (polls 127.0.0.1:8081 directly)
  │                 └── lxterminal on Pi desktop  (autostart, re-attach loop)
  └── /api/v1/snapshot  (JSON contract for any other consumer)
```

Port assignments:

| Port | Owner | Purpose |
|---|---|---|
| 80 | nginx | Canonical display/API entry point |
| 8080 | nginx | Legacy Kindle bookmark; identical proxy behavior to port 80 |
| 8081 | supervisor | Raw web/API server, loopback consumers only |

## Operator Controls (terminal display)

On an interactive tty the `api_terminal_display` client takes single keypresses:
`p` power, `w` weather, space toggles the two, `q` quits. `t` opens **tune
mode** for nudging a controller's scalar charge voltage from the console, and
`o` opens **options mode** for controller maintenance switches.

Options mode is staged-commit. `0` and `1` toggle the pending operational state
for the Classic (CC0) and EPEver (CC1), respectively; either or both may be
staged. `Enter` applies and `Esc` cancels. A disabled controller is treated as
deliberately absent: its device actor stops bus polling and rejects control
writes, it is removed from allocation and normal displays, and its individual
telemetry/settings values are not stored. The metric store continues a
per-snapshot `user_enabled=0` heartbeat and records a `user_enabled_changed`
event at each transition. The switch is persisted in the supervisor runtime
state and survives restarts.

Tune mode is **staged-commit**: `+`/`-` stage a change to the selected row
(shown as `→ target (Δ)` in the overlay) but send nothing; the change is applied
only on `Enter`. The overlay has a row per controller voltage plus a **CCL
scaling** row. `0`/`1` select a controller, `s` selects CCL scaling, Tab cycles
through them; `[`/`]` cycle the step size (voltages: 0.05 / 0.1 / 0.2 V; scaling:
5 / 10 / 20 points), `Esc` (or `q`) cancels without sending. This keeps a stray
keypress from moving the system — applying a change always takes a deliberate
Enter.

The CCL scaling row tunes the allocator's CCL scaling factor — see
[charge-current-allocation.md](../charge-current-allocation.md). It is shown and
nudged as a percent (10 points at a time by default — big enough to clear the
allocator's 5 A deadband near the knee), committed via
`POST /api/v1/control/ccl-scaling-factor`. The committed value is persisted and
survives a supervisor restart.

Guard rails, outermost last:

- The staging gate (`t` to arm) means normal keys never touch voltage.
- A per-session budget caps how far one tune session can wander from where it
  opened (±0.5 V); go further by committing and re-arming.
- Tune mode auto-disarms after ~20 s idle, so it's never left armed.
- On commit the client sends one `delta_v` per dirty controller to
  `POST /api/v1/control/charge-controller/voltage` (default `--control-url`
  `http://127.0.0.1:8081`, the loopback control surface — not the nginx
  display port).
- The supervisor's BMS-CVL guard and 1.0 V per-call delta cap are the hard
  backstops regardless of what the client sends; a refused write is shown in
  the overlay, and the setpoint only advances on a confirmed readback.

## Kindle Primary-Page Layout

The Kindle renderer estimates the vertical space used by the primary page from
the section headings and table rows it is about to emit. When the complete
Inverter/Charger group fits within the e-ink content area, that group is moved
from **More Power Info** onto the primary page. This commonly happens when a
charge controller is user-disabled for maintenance. If a second controller,
extra telemetry rows, or warnings consume the space, the inverter group stays
on the details page; the group is never split between pages.

## Status Annotations and Device Reachability

### Status annotations

`snapshot_status_annotations(snapshot)` in `supervisor.py` is the single source
of truth for display-level network annotation text. All renderers (terminal,
api_terminal_display, Kindle HTML, browser HTML, weather pages) call it rather
than deriving annotations themselves.

Rules (applied in order, first match wins):
- `lan_reachable is False` → `["LAN offline"]`
- `wan_reachable is False` → `["WAN offline"]`
- otherwise → `[]`

LAN supersedes WAN because WAN goes down as a side effect of LAN outage — if
the router is off, both fail together and "LAN offline" is the root cause.
Showing "WAN offline" during a LAN outage would be misleading.

LAN loss is declared only after two consecutive failed gateway probes; one
dropped ICMP reply preserves the last healthy state. A successful probe clears
the failure count and restores LAN state immediately. This prevents a transient
ping miss from hiding the TCP-connected Classic as unreachable.

Gateway reachability is not, by itself, authoritative for the Classic. The
display hides cached Classic telemetry immediately only when the gateway probe
and the Classic actor's own latest Modbus poll both fail. A successful Classic
poll is stronger, path-specific evidence and remains displayed even if ICMP to
the router is unavailable. Failed ping exit/timeout details are retained in the
supervisor journal for diagnosis.

`snapshot_severity_text()` composes the parenthetical form shown on most screens
(`"OK"`, `"WARN (LAN offline)"`, etc.). Weather pages pass annotations through
separately so the Kindle weather header can format them as `"OFFLINE as of
HH:MM TZ"` without the status-severity shape.

### Device reachability

Devices that are **configured** (not in `disabled_devices`) but **currently
unreachable** show in all displays rather than vanishing. The mechanism:

`_solar_api_payload()` in `web_display.py` emits a stub entry for each
configured-but-unavailable device:

```json
{"id": "classic.0", "device": {"vendor": "MidNite", ...}, "status": "unreachable"}
```

All three renderers check `controller.get("status") == "unreachable"` and emit
a heading-only row (`"Charge Controller 0 (Classic) — UNREACHABLE"`) with no
data rows. This keeps the section at its expected index and lets operators
confirm the system is aware of the missing device, not silently ignoring it.

The `disabled_devices` frozenset on `SupervisorSnapshot` distinguishes
"not configured" (no device installed) from "configured but offline". A device
not in `disabled_devices` that has no data gets UNREACHABLE; a device in
`disabled_devices` is simply absent from the payload.

### Stale-cache suppression on LAN outage

The reader grace window (`expire_after_s`, default 60 s for Classic) is
designed to absorb RS485/CAN transient glitches without surfacing per-device
errors. It is not appropriate for LAN outages:

- A LAN outage is a known root cause, not a transient glitch.
- Classic (Modbus TCP) is definitively offline when the LAN is down — there is
  no ambiguity.
- Showing stale cached values for the full grace window would mislead operators
  into thinking the device is still live.

`_collect_from_readers()` in `supervisor.py` therefore bypasses the cache check
for Classic when `self._network_monitor.lan_reachable is False`. The cached
value is dropped immediately, the device transitions to UNREACHABLE in all
displays at the same moment the status line shows "LAN offline", and Classic
read errors are suppressed (LAN outage is the root cause; per-device noise
would be redundant).

## Restart Behavior

`sudo systemctl restart offgrid-supervisor` is the standard deploy action and
the whole chain is designed around it:

1. The supervisor restarts; port 8081 is down for a few seconds.
2. nginx keeps 80/8080 up and serves a 200 "stand by" page with a 5-second
   meta refresh, so the Kindle keeps refreshing until the dashboard is back.
3. `BindsTo=offgrid-supervisor.service` stops and restarts
   `offgrid-console.service` with it, so the console process always runs code
   consistent with the live API.
4. The console service recreates the tmux session; the desktop window's
   `open-offgrid-console` script loses its attachment, loops, and re-attaches
   within ~2 seconds.

`BindsTo` propagates **stops** but not **starts**, which used to leave the
console dead after a `stop`/`start` cycle (e.g. stopping the supervisor to
free the EPEver adapter for a direct write). The supervisor unit now carries
`Upholds=offgrid-console.service` (systemd 249+), so systemd revives the
console automatically once the supervisor is active again — a plain
`systemctl start offgrid-supervisor` is enough; no need for `restart` or to
start both units. (Fixed 2026-06-11 after the console dropped during the
EPEver charge-voltage sync.)

## Failure Modes Found 2026-06-09

Both regressions surfaced the same way: frequent supervisor restarts while
integrating the Magnum RS-485 device turned previously-rare race windows into
certainties.

### Kindle display dead after supervisor restart

The Kindle was bookmarked directly to `:8080` when the supervisor itself
listened there, predating nginx. The nginx retry page (added for an earlier
blank-display incident) therefore never protected it. When the Kindle's
60-second meta refresh landed inside a restart window, WebKit got connection
refused, displayed its native error page, and the meta-refresh chain died —
the display stays dead until someone reloads it at the wall.

Fix: supervisor moved to 8081; nginx listens on both 80 and 8080 so the
existing Kindle bookmark gets retry-page protection with no change on the
Kindle. Lesson: the Kindle's only recovery mechanism is the meta refresh in
whatever page it is currently showing. Any response it can render must carry
a refresh tag, and it must never see a connection-level failure.

### Desktop console window closed permanently

The autostart lxterminal ran a script ending in `exec tmux attach`. When the
console service restart killed the tmux session, the attach exited, the
script was gone (exec), and the window closed. The LXDE autostart entry only
fires at desktop login, so nothing brought it back.

Fix: `open-offgrid-console` (tracked in `config/desktop/`) loops forever,
re-attaching whenever the session reappears.

## Deployed Configuration

| Repo file | Deploy target |
|---|---|
| `config/systemd/offgrid-supervisor.service` | `/etc/systemd/system/` |
| `config/systemd/offgrid-console.service` | `/etc/systemd/system/` |
| `config/nginx/offgrid-supervisor.conf` | `/etc/nginx/sites-available/` |
| `config/desktop/open-offgrid-console` | `~/.local/bin/` |
| `config/desktop/offgrid-console.desktop` | `~/.config/autostart/` |
| `scripts/run-console-tmux.sh` | run in place from the repo checkout |

Verification after deploying display-chain changes:

```sh
# Retry page while supervisor is down (expect 200 + "supervisor unreachable")
sudo systemctl stop offgrid-supervisor
curl -s -A 'Kindle/3.0' http://127.0.0.1:8080/ | grep -o 'supervisor unreachable'
sudo systemctl restart offgrid-supervisor   # restart, not start: BindsTo
sleep 6
systemctl is-active offgrid-supervisor offgrid-console nginx
tmux list-clients -t offgrid-console        # desktop window re-attached
```
