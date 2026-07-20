# Supervisor API

The off-grid supervisor exposes HTTP endpoints for display clients, operator
control scripts, and future apps. One process owns hardware polling, local
persistence, the latest snapshot cache, and device writes. Display clients
consume this API and must not poll hardware or write metrics.

## Process Boundary

`offgrid-supervisor.service` is the only always-on hardware collector:

- Reads Classic, battery CAN, ambient sensors, and future device adapters.
- Computes status conditions and load summaries.
- Writes local metrics and rolling logs at configured cadences.
- Serves HTML and JSON API endpoints from the latest cached snapshot.

Display clients are read-only:

- `offgrid-terminal-display` renders from `/api/v1/snapshot`.
- Kindle/browser clients render from `/`, `/kindle`, or `/display`.
- Future iOS clients can read `/api/v1/snapshot` for live state and object storage for history.

Control scripts use `POST /api/v1/control/...` instead of opening RS485
adapters directly. The supervisor queues writes on the same per-device actor
thread that performs polling, so reads and writes to a device cannot race.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness probe (process up and producing snapshots) |
| `GET` | `/api/v1/health` | Machine-readable health: degraded vs down, with per-device checks |
| `GET` | `/api/v1/snapshot` | Complete latest display snapshot |
| `POST` | `/api/v1/control/charge-controller/voltage` | Write one scalar charge voltage, addressed by controller number |
| `POST` | `/api/v1/control/charge-controller/charge-settings` | Write charge settings to a controller addressed by number |
| `POST` | `/api/v1/control/charge-controller/charging` | Enable/disable a controller's charge output, addressed by number |
| `POST` | `/api/v1/control/charge-controller/sync` | Copy charge settings from one controller to another by number |
| `POST` | `/api/v1/control/ccl-scaling-factor` | Set/nudge the CCL scaling factor (allocator policy, in-memory) |
| `GET` | `/api/v1/control/allocation/status` | Return current allocation override state (paused, manual ceilings) |
| `POST` | `/api/v1/control/allocation/pause` | Pause or resume allocator writes (evaluation continues) |
| `POST` | `/api/v1/control/allocation/manual-limit` | Set or clear a per-controller current ceiling |
| `POST` | `/api/v1/control/magnum/charge-settings` | Reserved Magnum charge-setting surface; currently returns `501 not_implemented` |
| `GET` | `/api/v1/status` | Future supervisor/export/storage status |
| `GET` | `/api/v1/metrics/latest` | Future normalized latest metric sample list |

Only the endpoints listed above are implemented today; `/api/v1/status` and
`/api/v1/metrics/latest` are placeholders.

**Controller numbers:** `0` = MidNite Classic, `1` = EPEver. The canonical
parameter name is `"controller"` (integer). Brand-name path aliases
(`/classic/charge-settings`, `/epever/charge-settings`, `/epever/sync-from-classic`,
`/epever/charging`) are kept for backward compatibility but the number-based paths
are preferred for scripts and future automation.

## HTTP Behavior

- API responses use `Content-Type: application/json`.
- API responses use `Cache-Control: no-store`.
- Timestamps are ISO 8601 strings with timezone offsets.
- Responses include `schema_version`.
- Clients must ignore unknown fields.
- New fields may be added without changing `schema_version`.
- Existing fields should not be renamed or removed without a new schema version.

### Individually monitored loads

`GET /api/v1/snapshot` includes `monitored_loads`, one object per configured
Tasmota monitor, sorted by supervisor key:

```json
{
  "name": "refrigeration",
  "host": "192.168.0.210",
  "voltage_v": 121.4,
  "current_a": 0.82,
  "power_w": 87.0,
  "apparent_power_va": 99.0,
  "reactive_power_var": 47.0,
  "power_factor": 0.88,
  "energy_today_kwh": 0.456,
  "energy_yesterday_kwh": 1.234,
  "energy_total_kwh": 123.456,
  "rolling_average_power_w": 100.2
}
```

See [Individual load metering](../subsystems/load-metering.md) for cadence,
counter semantics, and the distinction between generic logging and explicit
display annotations.

## Control

Control endpoints accept JSON objects and return JSON. They are intended for
local operator scripts on the Pi or a trusted management network; they are not
an unauthenticated public Internet API.

`GET /api/v1/snapshot` exposes charge-controller timer readback under each
solar controller's `settings` object:

- Classic: `absorb_time_minutes`.
- EPEver: `absorb_time_minutes`; `equalize_time_minutes` is the
  equalize-stage duration.

`POST /api/v1/control/charge-controller/voltage` accepts:

```json
{
  "controller": 0,
  "voltage_v": 56.3,
  "dry_run": false
}
```

`controller` is the displayed charge-controller number: `0` is the MidNite
Classic and `1` is the EPEver. The endpoint implements the shared LiFePO4
scalar-voltage policy:

- Classic: absorb, equalize, and max temp-comp are set to `voltage_v`; float is
  set to `voltage_v - 0.1` because the Classic requires float below absorb.
- EPEver: boost/absorb, float, and equalize are set to `voltage_v`; boost
  recovery / BVR is set to `voltage_v - 1.0`.

All resulting charge voltages are guarded against the BMS-published CVL. Pass
`"dry_run": true` to return the planned register-level values without writing.
The aliases `controller_number` and `charge_controller_number` are also
accepted for `controller`.

Instead of an absolute `voltage_v`, send a signed `delta_v` to *nudge* the
controller's current scalar setpoint:

```json
{ "controller": 0, "delta_v": 0.1, "dry_run": false }
```

This is a read-modify-write: the supervisor reads the controller's current
scalar setpoint on the device's actor thread (Classic absorb / EPEver boost),
adds `delta_v`, applies the same scalar policy, and writes it back. Exactly one
of `voltage_v` or `delta_v` must be supplied; supplying both is a `400`.
`delta_v` magnitude is capped at 1.0 V per call (a runaway-client backstop —
larger moves should use `voltage_v`); the resolved target is still guarded
against BMS CVL like any other write.

Voltage responses include `previous_voltage_v` (the pre-write scalar, `null`
for an absolute set), the resolved `voltage_v`, the requested `delta_v`
(`null` for an absolute set), and `confirmed` — `true` when the controller's
readback matched the target, `null` on a dry run.

`POST /api/v1/control/ccl-scaling-factor` sets or nudges the **CCL
scaling factor** — the allocator knob that scales the BMS charge-current limit
(CCL) down to a working charge budget. The default is `0.5` (50%), and it only
bites near the taper knee, where the BMS CCL has dropped below
`bms_knee_ccl_baseline_a`; above the baseline charging is unconstrained and the
factor has no effect. It accepts:

```json
{ "delta": 0.05, "dry_run": false }
```

or an absolute value:

```json
{ "factor": 0.55 }
```

Exactly one of `factor` or `delta` must be supplied. A `delta` is a
read-modify-write on the live allocator policy. The factor is clamped to
`0.05`–`1.0`; a request resolving outside that band is a `400` (not silently
clamped). `delta` magnitude is capped at `0.25` per call. The response is
`{ "ok": true, "previous_factor": 0.5, "factor": 0.55, "delta": 0.05, "dry_run": false }`.

A successful write is **persisted** to the supervisor's runtime-state file
(`--runtime-state-path`, default `/var/lib/offgrid/runtime-state.json`) and
reloaded on the next start, so it survives a restart; the env default
(`CHARGE_CEILING_BMS_CCL_SCALING_FACTOR`, default `0.5`) applies only when the
state file has no value. It returns `409` when charge allocation is not running
(the knob would have no effect). The current value is published under the
snapshot's `allocation.ccl_scaling_factor`.

`POST /api/v1/control/charge-controller/enabled` changes one controller's
operational maintenance switch:

```json
{ "controller": 1, "enabled": false }
```

Controller `0` is the Classic and controller `1` is the EPEver. Disabling is a
single coherent action across control, telemetry polling/storage, allocation,
and display. The state is persisted in the same runtime-state file and exposed
in `snapshot.charge_controllers`, independently of the `solar` list so a
disabled controller remains observable even though its telemetry is omitted.

`POST /api/v1/control/classic/charge-settings` accepts any subset of:

```json
{
  "current_limit_a": 80.0,
  "absorb_voltage_v": 55.4,
  "float_voltage_v": 54.7,
  "equalize_voltage_v": 55.4,
  "absorb_time_minutes": 30,
  "max_temp_comp_voltage_v": 56.8
}
```

Classic voltage targets are guarded against the BMS-published charge-voltage
limit (CVL). `absorb_time_minutes` is written to the Classic absorb timer register
after conversion to the controller's native seconds register.

Successful EPEver charge-setting writes return the controller readback:

```json
{
  "ok": true,
  "device": "epever",
  "settings": {
    "battery_type": "User",
    "battery_type_code": 0,
    "charging_limit_voltage_v": 60.0,
    "equalize_voltage_v": 55.6,
    "absorb_voltage_v": 55.6,
    "boost_voltage_v": 55.6,
    "float_voltage_v": 54.7,
    "max_charging_current_a": 80.0,
    "absorb_time_minutes": 90,
    "equalize_time_minutes": 0
  }
}
```

`POST /api/v1/control/epever/charge-settings` accepts any subset of:

```json
{
  "absorb_voltage_v": 55.6,
  "equalize_voltage_v": 55.6,
  "float_voltage_v": 54.7,
  "bulk_recovery_voltage_v": 54.9,
  "max_charging_current_a": 80.0,
  "absorb_time_minutes": 90,
  "equalize_time_minutes": 0
}
```

`absorb_voltage_v` maps to the EPEver manual's BCV / Bulk Charging Voltage.
`boost_voltage_v` remains accepted as a backward-compatible alias.
`bulk_recovery_voltage_v` is accepted as an alias for
`boost_reconnect_voltage_v` and maps to BVR / Bulk Voltage Recovery, the
threshold below float where the controller may re-enter boost/bulk after
having dropped to float.
`absorb_time_minutes` maps to the EPEver Boost/Bulk charging time register
(`0x9014`) in minutes. `equalize_time_minutes` maps to `0x9015`.

The supervisor-side EPEver writer preserves the rest of the `0x9007..0x9012`
voltage block and refuses unsafe or unsupported requests, including non-User
battery type, boost above equalize, charge voltages above the EPEver
charging-limit ceiling, and any requested charge-voltage setpoint above the
BMS-published charge-voltage limit (CVL). If BMS CVL is unavailable, voltage
writes are refused rather than guessed.

`POST /api/v1/control/epever/sync-from-classic` reads the supervisor's current
Classic settings and writes the corresponding EPEver settings. It accepts:

```json
{
  "voltage_offset_v": 0.3,
  "no_current": false
}
```

`voltage_offset_v` defaults to `0.0` and is added to the Classic absorb,
float, and equalize setpoints before writing EPEver boost, float, and equalize.
The EPEver equalize target is also raised to at least the target boost voltage,
because the controller rejects boost above equalize. The same BMS CVL guard is
applied before writing.

`POST /api/v1/control/charge-controller/charge-settings` is the canonical
number-addressed form. Pass `"controller": 0` for Classic settings or
`"controller": 1` for EPEver settings, alongside any subset of the
device-specific fields described above for the brand-name aliases. The
brand-name paths `/api/v1/control/classic/charge-settings` and
`/api/v1/control/epever/charge-settings` are backward-compatible aliases that
route to the same handlers without requiring a `"controller"` field.

`POST /api/v1/control/charge-controller/charging` accepts:

```json
{ "controller": 1, "enabled": false }
```

Currently only controller `1` (EPEver) supports a hardware charging toggle via
its charge coil; a request for controller `0` returns `400`. The alias
`/api/v1/control/epever/charging` is kept for backward compatibility.

`POST /api/v1/control/charge-controller/sync` copies charge settings from one
controller to another. The canonical form accepts:

```json
{ "source": 0, "target": 1, "voltage_offset_v": 0.3, "no_current": false }
```

Currently only `source: 0, target: 1` (Classic → EPEver) is supported. The alias
`/api/v1/control/epever/sync-from-classic` is kept for backward compatibility and
implies `source: 0, target: 1`.

`POST /api/v1/control/magnum/charge-settings` accepts the planned shape:

```json
{
  "absorb_voltage_v": 54.4,
  "float_voltage_v": 54.4,
  "absorb_time_hr": 1.0,
  "charger_amps_pct": 80,
  "shore_amps": 30
}
```

but currently returns HTTP `501` with `reason: "not_implemented"`. The Magnum
library can send remote packets, but this system has not yet verified a safe
read-modify-write primitive that preserves the active remote configuration.
Voltage targets sent to this endpoint are still checked against BMS CVL before
the backend returns `501`.

### Allocation override

Three endpoints let an operator pause the allocator or cap individual controller
outputs without stopping the supervisor. All state is **in-memory only** — it
resets to allocator-controlled on supervisor restart. The display immediately
reflects any override (no waiting for the next allocation cycle).

`GET /api/v1/control/allocation/status` returns current override state:

```json
{
  "paused": false,
  "manual_limits_a": { "0": null, "1": null }
}
```

Keys in `manual_limits_a` are string controller numbers. `null` means
allocator-controlled; a number means a manual ceiling is in effect.

`POST /api/v1/control/allocation/pause` pauses or resumes allocator writes:

```json
{ "paused": true }
```

While paused, the allocator continues to evaluate and log decisions but writes
nothing to either controller. Returns `409` if charge allocation is not enabled.
Response: `{ "ok": true, "previous_paused": false, "paused": true }`.

`POST /api/v1/control/allocation/manual-limit` sets or clears a per-controller
current ceiling:

```json
{ "controller": 0, "limit_a": 5.0 }
```

Pass `"limit_a": null` to clear the ceiling and return to allocator control. A
manual ceiling acts as a hard cap: the allocator evaluates normally and its
output for that controller is clamped to `min(allocator_target, ceiling)`. The
ceiling is written to the device immediately and on every subsequent allocation
cycle. Returns `409` if charge allocation is not enabled. Response:

```json
{ "ok": true, "controller": 0, "previous_limit_a": null, "limit_a": 5.0 }
```

**`scripts/allocation-override.py`** is the operator CLI for these endpoints:

```
# Status
python scripts/allocation-override.py status

# Pause / resume allocator writes
python scripts/allocation-override.py pause
python scripts/allocation-override.py resume

# Set a per-controller ceiling
python scripts/allocation-override.py limit 0 5     # cap Classic at 5 A
python scripts/allocation-override.py limit 0 --clear  # restore allocator control
python scripts/allocation-override.py limit 1 30    # cap EPEver at 30 A
```

The Charge Allocation display group shows `(paused)` in the header when the
allocator is paused, and `→ N A manual ceiling` on a controller row when a
ceiling is active for that controller.

## Health

Two endpoints with deliberately different jobs, so a watcher can tell *degraded*
from *down*:

- **`GET /healthz`** — liveness. Returns `200 ok` whenever the supervisor can
  produce a snapshot (process and poll loop alive), and `503` only when it
  cannot produce one at all. An offline device does **not** fail it — restarting
  the supervisor would not bring the device back. This is the endpoint a
  restart-watcher (systemd, an uptime monitor) should poll.
- **`GET /api/v1/health`** — diagnostic health. Maps the snapshot's severity to
  HTTP: `OK`/`WARNING` → `200`, `ERROR` → `503`. A single device offline is
  `WARNING` (degraded, still `200`); `503` is reserved for a critical condition
  such as battery overvoltage. `ok` is `true` for any non-`ERROR` state.

The `checks` object reports per-device status (`ok` | `disabled` | `offline` |
`error`) with a `reason` and the error `detail`, so a consumer sees *which*
device is degraded and *what was observed* — not just the overall verdict.
`disabled` means no adapter is configured, so no read is attempted (distinct
from `offline`, which means a read was tried and returned nothing). `reason` is
classified only from the observed signature, never an inferred root cause:

- `disabled` — no adapter configured for this device.
- `transport_absent` — the serial port/adapter is not present (`Could not open …`).
- `no_response` — the port opened but the remote device stayed silent (Modbus timeout).
- `no_data` — `offline`: no telemetry and no captured error.
- `unknown` — an error that doesn't match a known signature (`detail` carries it).
- `null` — `ok`.

`checks.telemetry` additionally reports recorder state. `warning` means samples
are safely buffering in the SD fallback because the primary SSD store is
unavailable; `error` means both primary and fallback writes failed. Either is
surfaced as an overall `WARNING` with HTTP 200 so the transport-recovery
watchdog does not reboot a functioning supervisor for a storage problem.

Example (one controller offline — degraded, HTTP `200`):

```json
{
  "schema_version": 2,
  "ok": true,
  "status": "WARNING",
  "captured_at": "2026-06-05T20:55:00+00:00",
  "age_seconds": 2,
  "errors": ["EPEver read failed: Modbus timeout"],
  "conditions": [],
  "checks": {
    "classic": {"status": "ok", "reason": null, "detail": null},
    "epever": {"status": "error", "reason": "no_response", "detail": "EPEver read failed: Modbus timeout"},
    "battery": {"status": "ok", "reason": null, "detail": null},
    "magnum": {"status": "disabled", "reason": "disabled", "detail": null},
    "ambient": {"status": "ok", "reason": null, "detail": null}
  }
}
```

## Snapshot

`GET /api/v1/snapshot` returns the current display model. This is the primary API for terminal, web, Kindle, and future mobile live-status clients.

Each charge controller's `charge_stage` is a `{ canonical, vendor }` pair: `canonical` is the normalized industry-standard stage shared across controllers (see [charge-controller.md](../subsystems/charge-controller.md#charge-stage-vocabulary)), and `vendor` is the controller's native word, present only when it differs from the canonical (otherwise `null`). Clients display the canonical and, if `vendor` is set, the native word in parens — no vendor-specific knowledge required.

Example shape:

```json
{
  "schema_version": 1,
  "site_id": "cabin",
  "captured_at": "2026-06-05T20:55:00+00:00",
  "age_seconds": 2,
  "status": {
    "ok": true,
    "severity": "OK",
    "errors": [],
    "conditions": []
  },
  "battery": {
    "soc_percent": 91,
    "soh_percent": 100,
    "voltage_v": 53.2,
    "current_a": -4.1,
    "power_w": -218,
    "temperature_c": 18.5,
    "cell_min_v": 3.325,
    "cell_max_v": 3.331,
    "cell_delta_mv": 6,
    "charge_enabled": true,
    "discharge_enabled": true,
    "charge_voltage_limit_v": 56.0,
    "charge_current_limit_a": 200.0
  },
  "solar": [
    {
      "id": "classic.0",
      "label": "Classic 200",
      "battery_voltage_v": 53.1,
      "battery_current_a": 12.4,
      "battery_power_w": 659,
      "pv_voltage_v": 91.2,
      "pv_current_a": 7.4,
      "daily_energy_kwh": 3.8,
      "daily_amp_hours_ah": 72,
      "charge_stage": { "canonical": "Bulk", "vendor": "BulkMppt" },
      "state": "MPPT or regulating voltage"
    }
  ],
  "load": {
    "current_a": 4.0,
    "power_w": 212,
    "rolling_average_a": 3.5,
    "rolling_average_w": 184,
    "estimated_autonomy_hours": 46.0
  },
  "ambient": {
    "temperature_c": 17.8,
    "humidity_percent": null
  }
}
```

## Future Work

- Add long-poll support with `GET /api/v1/snapshot?wait=30`.
- Add `/api/v1/status` with export status, row counts, and storage health.
- Add `/api/v1/metrics/latest` after metric storage cadence and retention are corrected.
- Add install/deploy checks that fail if more than one process is writing `/srv/telemetry/data/metrics.sqlite`.
