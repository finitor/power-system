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
