# Store-And-Forward Metrics

The supervisor treats local SQLite as the authoritative telemetry log. WAN export is best-effort and can run only when a Starlink or other internet window is available.

## Current Direction

Use S3-compatible object storage, such as Cloudflare R2 or Backblaze B2, as a durable object-storage mailbox:

1. `offgrid-supervisor` records flat metric samples and irregular events in `/srv/telemetry/data/metrics.sqlite` on the SSD-backed data volume.
2. `offgrid-r2-export` reads unexported local rows from SQLite.
3. The exporter writes bounded gzip-compressed NDJSON batches to the bucket.
4. Only after the object store accepts the object should the exporter mark those rows exported.
5. Downstream consumers are intentionally undecided. A future iOS app, importer, or dashboard can read batch objects and deduplicate by a stable exported record id.

Delivery should remain at-least-once. If the Pi uploads a batch and then loses power before marking rows exported, the next run may upload the same records again. Consumers must use exported record ids as idempotency keys.

## Local SQLite Shape (decision 0003)

One canonical flat model: scalar telemetry goes to `samples`, irregular events to `events`. Both carry a content-hashed identity column (UNIQUE), so merging two stores is an idempotent `INSERT OR IGNORE` union — the basis for the SSD-removal SD-fallback design and for consumer-side dedup.

`samples` records one row per scalar at the durable metrics cadence, currently 60 seconds:

| Column | Purpose |
|---|---|
| `id` | Local row id (diagnostic only) |
| `sample_id` | sha256 of the row content; durable identity and dedup key |
| `captured_at` | Sample timestamp, local-offset ISO 8601 |
| `source` | Producer, e.g. `classic.0`, `epever.1`, `battery`, `magnum`, `load`, `ambient`, `weather`, `supervisor` |
| `metric` | Metric name within the source; new metrics need no schema change |
| `value` / `text` | Numeric or text payload |
| `unit` | Optional unit annotation, e.g. `V`, `A`, `W/m2` |
| `tags_json` | Per-sample dimensions (device id, cell index) without migration |
| `exported_at`, `export_batch_id` | Stamped by the exporter after upload |

`events` records irregular occurrences (taper decisions, inverter on/off and LBCO cut-outs) with the same shape: `event_id` content hash, `captured_at`, `source`, `event`, and a small `detail_json` blob.

Charge-controller settings are sampled continuously into `samples` (`classic.0.settings`, `epever.1.settings`) rather than kept in a separate change-tracked table; weather conditions land as `weather` source samples (`temperature`, `cloud_cover`, `shortwave_radiation`, `direct_radiation`, `diffuse_radiation`, `direct_normal_irradiance`, `aurora_probability`, …).

`export_batches` keeps one row per object-storage batch attempt/result. The exporter builds/uploads a batch without holding a SQLite write transaction, then stamps the exported rows (`exported_at`, `export_batch_id`) and appends the batch row in a short transaction after object storage accepts the upload.

Useful inspection queries:

```sql
SELECT captured_at, source, metric, value, text
FROM samples
ORDER BY id DESC
LIMIT 20;

SELECT captured_at, source, event, detail_json
FROM events
ORDER BY id DESC
LIMIT 20;

SELECT COUNT(*) FROM samples WHERE exported_at IS NULL;
```

## Object Format

Each batch holds one table and one local capture date, under a hive-style partition (oldest unexported date drains first):

```text
metrics/samples/date=YYYY-MM-DD/YYYYMMDDTHHMMSSZ-<batch_id>.ndjson.gz
metrics/events/date=YYYY-MM-DD/YYYYMMDDTHHMMSSZ-<batch_id>.ndjson.gz
```

Serialization is gzipped NDJSON, not Parquet: the Pi is 32-bit (armv7l) and pyarrow ships no wheels for it (decision 0003, amended). DuckDB reads `.ndjson.gz` from S3 natively, and the layout already matches Parquet conventions, so a future 64-bit upgrade swaps only the serializer. Rows per object are uniform:

```json
{"record_type":"sample","site_id":"cabin","record_id":"<sample sha256>","local_row_id":123,"captured_at":"2026-06-05T12:00:00+00:00","source":"battery","metric":"soc","value":91.0,"text":null,"unit":"%","tags":{}}
{"record_type":"event","site_id":"cabin","record_id":"<event sha256>","local_row_id":4,"captured_at":"2026-06-05T12:00:00+00:00","source":"magnum","event":"lbco_cutout","detail":{"fault":"LOW_BAT"}}
```

`local_row_id` is diagnostic only. Consumers should use the content-hash `record_id` as the idempotency key.

## Ad Hoc Queries with DuckDB

DuckDB has no armv7l wheel either, so analysis runs on the Mac (or any 64-bit box), spanning the bucket and a synced copy of the local store in one session:

```sql
INSTALL httpfs; LOAD httpfs;
CREATE SECRET b2 (TYPE s3, KEY_ID '...', SECRET '...',
                  ENDPOINT 's3.<region>.backblazeb2.com');

-- archive in the bucket (partition-pruned by the date= path)
SELECT captured_at, value
FROM read_json('s3://<bucket>/metrics/samples/date=2026-06-*/*.ndjson.gz')
WHERE source = 'battery' AND metric = 'soc';

-- live store (scp blueberry.local:/srv/telemetry/data/metrics.sqlite first)
ATTACH 'metrics.sqlite' (TYPE sqlite);
SELECT captured_at, value FROM metrics.samples
WHERE source = 'battery' AND metric = 'soc';
```

## Cloudflare R2 Configuration

Set these on the Pi for R2:

```sh
METRICS_DB_PATH=/srv/telemetry/data/metrics.sqlite
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
METRICS_DB_PATH=/srv/telemetry/data/metrics.sqlite
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

`config/systemd/offgrid-metrics-export.timer` runs daily at 12:05 local time (re-enabled 2026-06-12; deploy.sh keeps it enabled).

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
