# Escalation Reporting via Healthchecks.io  *(PLAN — not yet provisioned)*

How off-grid alerts reach a human, routed through [Healthchecks.io](https://healthchecks.io)
as a single notification plane. **Status: design + code seam landed; no account or
checks exist yet.** The watchdog already calls the seam (`notify()` in
`scripts/supervisor-watchdog.sh`) but it is inert until `HC_*` URLs are set in
`/etc/offgrid-power.env`. Nothing in this doc is live.

Throughout: the Pi is `blueberry.local`, the supervisor serves health at
`http://127.0.0.1:8081/api/v1/health`, and env lives in `/etc/offgrid-power.env`.

---

## 1. Why route through Healthchecks at all

The Pi runs an off-grid power system whose worst failures **take the network with
them**. On 2026-06-30 a 5 V brownout de-enumerated the USB bus — onboard eth, RS485,
Magnum serial, and CAN all dropped together; the supervisor ran blind ~2.5 h until a
hard power-cycle. An alert composed *at the moment of failure* often can't be sent,
because sending it needs the network that just died.

Two consequences drive the design:

1. **The reliable signal is absence, not presence.** A *dead-man's switch* — the Pi
   emits "I'm OK" on a cadence; a cloud service alerts when that signal *stops* —
   is the only thing that catches total death / no-network, precisely because the Pi
   is never responsible for reporting its own death.
2. **Consolidate everything in one plane.** Even alerts the Pi *could* self-send go
   through Healthchecks, so there is one dashboard, one routing config, one audit
   log — and the Pi sheds SMTP entirely (its only alert egress is HTTPS to
   `hc-ping.com`, which the heartbeat needs anyway). Smaller attack surface, one
   fewer secret.

> **Honest limit:** routing a self-reportable alert through `hc-ping` instead of SMTP
> does **not** help it escape a dead network — both need connectivity. The win is
> consolidation + the dead-man backstop catching the silence, not in-the-moment
> delivery during an outage.

## 2. What Healthchecks is (work with the grain)

A **binary status monitor** (up / grace / down) that notifies on **state
transitions**, not a severity-graded message bus or a generic email API. The Pi
drives state with pings:

| Ping | Effect |
|---|---|
| `POST {url}` | success → marks **up** (recovery email if it was down) |
| `POST {url}/fail` | marks **down** → notification fires, with the body embedded |
| `POST {url}/start` | marks a run started (for measuring duration / reboot windows) |
| `POST {url}/log` | records an event **without** changing state (breadcrumb; no email) |

Any ping may carry a **body payload** (status JSON, which transports are down,
battery SoC) — stored and shown in the email/dashboard. Re-`/fail`ing while already
down does **not** re-email (dedup by transition).

## 3. Max richness on the free tier

Hosted free tier gives **20 checks**, cron **and** period schedules with timezone +
grace, email plus many integrations, and per-check event history. It is also
open-source / self-hostable (removes all limits) if we outgrow it. We express
"escalating severity" — which Healthchecks has no native concept of — two ways:

- **One check per condition/severity** (we have 20 to spend). Severity = *which
  integrations a check carries*: a `*-warning` check → email only; a `*-critical`
  check → email + a louder channel (Pushover/Discord/PagerDuty free integrations).
- **Detail in the ping body**, so each notification reads as a status digest.

### Proposed check taxonomy

| Check slug | Driven by | Pings | Catches |
|---|---|---|---|
| `pi-liveness` | a Pi timer, when up + network | success on a schedule | **total death / no-network** (the dead-man) |
| `supervisor-watchdog` | `supervisor-watchdog.sh` *(seam already wired)* | `/fail` on reboot/cooldown, success on recovery | the blind-supervisor escalation |
| `supervisor-degraded` | a small notifier polling `/api/v1/health` | `/fail` on WARNING/ERROR edge, success on OK | degraded-but-alive (a transport down for hours) |
| `battery-low` | supervisor / notifier | `/fail` below SoC floor, success on recovery | slow-burn battery the Pi might not escalate |

`supervisor-watchdog` is the only one needed for the current escalation work;
the rest are the growth path.

## 4. Env-var contract (the integration seam)

The watchdog reads these from `/etc/offgrid-power.env` (already loaded via the
service `EnvironmentFile`). All optional — **unset = the seam is inert (log only)**:

```sh
# Ping URL for the "supervisor-watchdog" check (base URL, no trailing slash).
HC_SUPERVISOR_WATCHDOG_URL=https://hc-ping.com/<uuid>
```

Future checks follow the same `HC_<CHECK>_URL` convention (`HC_PI_LIVENESS_URL`,
`HC_SUPERVISOR_DEGRADED_URL`, `HC_BATTERY_LOW_URL`).

`scripts/supervisor-watchdog.sh::notify()` already POSTs `{url}` (recovery) or
`{url}/fail` (escalation/cooldown) with a descriptive body, `curl --retry 3 -m 10`,
and logs (does not fail the run) if the ping can't go out.

## 5. Provisioning steps (when ready to go live)

1. Create a Healthchecks.io account (or stand up a self-hosted instance).
2. Add a check named `supervisor-watchdog`. Schedule: **period mode** to start
   (this check is event-driven, not a heartbeat, so a long period + large grace —
   it only ever goes down via an explicit `/fail`). Add the **email** integration.
3. Copy its ping URL into `/etc/offgrid-power.env` as `HC_SUPERVISOR_WATCHDOG_URL`,
   then `sudo systemctl restart offgrid-supervisor-watchdog.timer` (picks up env on
   next run; the service reads `EnvironmentFile` each invocation).
4. Verify: `sudo env SUPERVISOR_WATCHDOG_HEALTH_URL=file:///tmp/down.json \
   SUPERVISOR_WATCHDOG_ARMED=0 sh scripts/supervisor-watchdog.sh` with a synthetic
   all-down payload, and confirm the `/fail` ping lands (check goes down → email).
5. Repeat for the other checks as their notifiers are built.

## 6. Open design question — intermittent backhaul

Off-season, the Starlink backhaul may be powered ~1 h in 24 for conservation, so the
Pi is *expected* to be silent ~23 h/day. The `pi-liveness` dead-man must therefore
expect a check-in aligned to the **connectivity window, not Pi uptime** — Healthchecks'
**cron-schedule mode** (`expect a ping during the window, grace = window + margin`)
handles this on the free tier. Detection latency is then inherently ~1 day (physics
of a 1 h/24 backhaul, not a tool limit). Deferred until the liveness layer is built;
captured here so it isn't lost. See the session discussion for the two-check
dead-vs-degraded split and per-window ping-retry pattern.

## 7. Related

- `scripts/supervisor-watchdog.sh` — the escalation watchdog and `notify()` seam.
- `config/systemd/offgrid-supervisor-watchdog.{service,timer}` — how it runs.
- `config/systemd/system-watchdog.conf` — the hardware-watchdog backstop.
- `docs/journal/2026-06-30.md` — the incident this all came from.
