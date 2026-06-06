# Store-And-Forward Metrics

The supervisor treats local SQLite as the authoritative telemetry log. WAN export is best-effort and can run only when a Starlink or other internet window is available.

## Current Direction

Use S3-compatible object storage, such as Cloudflare R2 or Backblaze B2, as a durable object-storage mailbox:

1. `offgrid-supervisor` records compact supervisor snapshots and sparse device-settings snapshots in `data/metrics.sqlite`.
2. `offgrid-r2-export` reads unexported local rows from SQLite.
3. The exporter writes bounded gzip-compressed NDJSON batches to the bucket.
4. Only after the object store accepts the object should the exporter mark those rows exported.
5. Downstream consumers are intentionally undecided. A future iOS app, importer, or dashboard can read batch objects and deduplicate by a stable exported record id.

Delivery should remain at-least-once. If the Pi uploads a batch and then loses power before marking rows exported, the next run may upload the same records again. Consumers must use exported record ids as idempotency keys.

## Local SQLite Shape

The active local schema intentionally avoids row-per-metric storage.

`supervisor_snapshots` records one compact JSON snapshot at the durable metrics cadence, currently planned for 60 seconds:

| Column | Purpose |
|---|---|
| `id` | Local row id |
| `captured_at` | Snapshot timestamp |
| `ok` | Boolean-ish integer status |
| `status` | Supervisor status text |
| `snapshot_json` | `/api/v1/snapshot`-compatible JSON payload |

`device_settings_snapshots` records settings/config state separately:

| Column | Purpose |
|---|---|
| `id` | Local row id |
| `captured_at` | Settings read timestamp |
| `device_id` | Device identifier, such as `classic.0` |
| `settings_hash` | Hash of stable settings values |
| `reason` | `startup`, `hourly`, or `changed` |
| `settings_json` | Stable settings payload without volatile timestamp fields |

The legacy `metric_samples` table may still exist for compatibility while old helpers are retired, but the supervisor no longer writes high-rate row-per-metric data.

Export state is append-only and separate from source rows:

| Table | Purpose |
|---|---|
| `export_batches` | One row per object-storage batch attempt/result |
| `export_batch_records` | Ledger linking exported source rows to a batch |

Source rows should remain immutable. The exporter should build/upload a batch without holding a SQLite write transaction, then append one batch row plus ledger rows in a short transaction after object storage accepts the upload.

Human-friendly views expose export status without mutating source tables:

| View | Purpose |
|---|---|
| `supervisor_snapshots_export_status` | Supervisor snapshots plus `export_batch_id`, `exported_at`, and object key |
| `device_settings_export_status` | Device settings snapshots plus `export_batch_id`, `exported_at`, and object key |

Useful inspection queries:

```sql
SELECT id, captured_at, status, exported_at
FROM supervisor_snapshots_export_status
ORDER BY id DESC
LIMIT 20;

SELECT id, captured_at, device_id, reason, exported_at
FROM device_settings_export_status
ORDER BY id DESC
LIMIT 20;
```

## Object Format

Batch object keys have this shape:

```text
metrics/YYYYMMDDTHHMMSSZ-<batch_id>.ndjson.gz
```

Each gzip-compressed NDJSON object distinguishes snapshot and settings records:

```json
{"record_type":"supervisor_snapshot","site_id":"cabin","record_id":"supervisor_snapshot:123","local_row_id":123,"captured_at":"2026-06-05T12:00:00+00:00","snapshot":{}}
{"record_type":"device_settings","site_id":"cabin","record_id":"device_settings:45","local_row_id":45,"captured_at":"2026-06-05T12:00:00+00:00","device_id":"classic.0","reason":"hourly","settings":{}}
```

`local_row_id` is diagnostic only. Consumers should not treat it as globally durable identity.

## Cloudflare R2 Configuration

Set these on the Pi for R2:

```sh
METRICS_DB_PATH=data/metrics.sqlite
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=...
R2_SITE_ID=cabin
R2_PREFIX=metrics
R2_REGION=auto
R2_EXPORT_LIMIT=5000
R2_EXPORT_MAX_BATCHES=1
R2_EXPORT_SLEEP_SECONDS=0
```

## Backblaze B2 Configuration

Set these on the Pi for B2:

```sh
METRICS_DB_PATH=data/metrics.sqlite
B2_APPLICATION_KEY_ID=...
B2_APPLICATION_KEY=...
B2_BUCKET=magpie-metrics
B2_ENDPOINT_URL=https://s3.<region>.backblazeb2.com
B2_REGION=<region>
B2_SITE_ID=cabin
B2_PREFIX=metrics
B2_EXPORT_LIMIT=5000
B2_EXPORT_MAX_BATCHES=1
B2_EXPORT_SLEEP_SECONDS=0
```

Use the bucket's S3 endpoint from the Backblaze bucket details page. The region is the middle part of that endpoint, for example `us-west-004` in `https://s3.us-west-004.backblazeb2.com`.

Run one export batch manually with:

```sh
offgrid-r2-export
```

## Daily Timer

`config/systemd/offgrid-metrics-export.timer` is currently disabled while the exporter is redesigned for the compact local schema. The intended schedule remains daily at 12:05 local time.

This timer is intended to line up with a future noon Starlink wake window when solar availability is usually good.

## Future Retry Mechanics

If the daily upload fails because Starlink, DNS, routing, TLS, credentials, or the object store are unavailable, rows remain unexported in SQLite and the next scheduled run retries them. That is the safe baseline.

Later, when Starlink is only awake for a short noon window, add a window-aware retry policy around the same exporter:

- Start Starlink before noon and wait for internet reachability.
- Run the compact-schema exporter until the local backlog is empty.
- If upload fails, retry every few minutes while the wake window remains open.
- Stop retrying before the planned Starlink shutdown.
- Record/export a local status metric such as `metrics_export.last_success_age_hours`.

Do not mark rows exported before object-store upload succeeds. Duplicate uploads are acceptable because downstream consumers deduplicate by exported record id.
