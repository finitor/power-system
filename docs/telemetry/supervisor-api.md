# Supervisor API

The off-grid supervisor exposes read-only HTTP endpoints for display clients and future apps. One process owns hardware polling, local persistence, and the latest snapshot cache. Display clients consume this API and must not poll hardware or write metrics.

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

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness probe (process up and producing snapshots) |
| `GET` | `/api/v1/health` | Machine-readable health: degraded vs down, with per-device checks |
| `GET` | `/api/v1/snapshot` | Complete latest display snapshot |
| `GET` | `/api/v1/status` | Future supervisor/export/storage status |
| `GET` | `/api/v1/metrics/latest` | Future normalized latest metric sample list |

Only `/api/v1/health` and `/api/v1/snapshot` are part of the first implementation slice.

## HTTP Behavior

- API responses use `Content-Type: application/json`.
- API responses use `Cache-Control: no-store`.
- Timestamps are ISO 8601 strings with timezone offsets.
- Responses include `schema_version`.
- Clients must ignore unknown fields.
- New fields may be added without changing `schema_version`.
- Existing fields should not be renamed or removed without a new schema version.

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

The `checks` object reports per-device status (`ok` | `offline` | `error`) with
a `reason` and the error `detail`, so a consumer sees *which* device is degraded
and *what was observed* — not just the overall verdict. `reason` is classified
only from the observed failure signature, never an inferred root cause:

- `transport_absent` — the serial port/adapter is not present (`Could not open …`).
- `no_response` — the port opened but the remote device stayed silent (Modbus timeout).
- `no_data` — `offline`: no telemetry and no captured error.
- `unknown` — an error that doesn't match a known signature (`detail` carries it).
- `null` — `ok`.

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
    "magnum": {"status": "offline", "reason": "no_data", "detail": null},
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
