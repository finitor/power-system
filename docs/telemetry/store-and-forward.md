# Store-And-Forward Metrics

The supervisor treats local SQLite as the authoritative telemetry log. WAN export is best-effort and can run only when a Starlink or other internet window is available.

## Current Direction

Use S3-compatible object storage (the live target is Backblaze B2) as a durable object-storage mailbox:

1. `offgrid-supervisor` records flat metric samples and irregular events in `/srv/telemetry/data/metrics.sqlite` on the SSD-backed data volume.
2. `offgrid-object-store-export` reads unexported local rows from SQLite.
3. The exporter writes bounded Parquet batches to the bucket.
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
| `captured_at` | Sample timestamp, UTC ISO 8601 with an explicit `+00:00` offset |
| `source` | Producer, e.g. `classic.0`, `epever.1`, `battery`, `magnum`, `load`, `ambient`, `weather`, `supervisor` |
| `metric` | Metric name within the source; new metrics need no schema change |
| `value` / `text` | Numeric or text payload |
| `unit` | Optional unit annotation, e.g. `V`, `A`, `W/m2` |
| `tags_json` | Per-sample dimensions (device id, cell index) without migration |
| `exported_at`, `export_batch_id` | Stamped by the exporter after upload |

`events` records irregular occurrences (taper decisions, inverter on/off and LBCO cut-outs) with the same shape: `event_id` content hash, `captured_at`, `source`, `event`, and a small `detail_json` blob.

Charge-controller settings are sampled continuously into `samples` (`classic.0.settings`, `epever.1.settings`) rather than kept in a separate change-tracked table; weather conditions land as `weather` source samples (`temperature`, `cloud_cover`, `shortwave_radiation`, `direct_radiation`, `diffuse_radiation`, `direct_normal_irradiance`, `aurora_probability`, …).

## Primary-store failure and recovery

If the SSD is absent or a primary write fails, the recorder immediately writes
to `/var/lib/offgrid/metrics-fallback.sqlite` on the boot card. The next
successful primary write merges the fallback into the primary with idempotent
content hashes and removes the fallback files. A primary failure therefore
creates a recoverable backlog rather than a telemetry gap.

This state is deliberately visible:

- the wall, browser, and Kindle displays show a `Telemetry storage` warning;
- `/api/v1/health` reports `checks.telemetry.status=warning` with the SQLite
  error and remains HTTP 200 (storage degradation must not trigger the I/O
  reboot watchdog);
- `/api/v1/snapshot` includes the full `telemetry` recorder state, including
  active store and last-write timestamps;
- `scripts/diag.sh` reports both the API state and fallback row range;
- deploy and `scripts/health-check.sh` fail unless the primary store is
  writable.

Recovery is normally to restore the WAL/SHM files to `offgrid:offgrid` mode 660;
the next successful write proves the primary and merges the fallback
automatically. Restarting `offgrid-supervisor` also runs a privileged preflight
that repairs ownership. The data directory is setgid with a default
group-writable ACL, but SQLite can restrict the inherited ACL mask when it
creates sidecars as another account. Run **all** live-database access as
`offgrid`, including `mode=ro` queries. For repeated or long analysis, use the
snapshot command documented in [Querying the Telemetry Log](querying.md).

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

Each batch holds one table and one UTC capture date, under a hive-style partition (oldest unexported date drains first):

```text
metrics/samples/date=YYYY-MM-DD/YYYYMMDDTHHMMSSZ-<batch_id>.parquet
metrics/events/date=YYYY-MM-DD/YYYYMMDDTHHMMSSZ-<batch_id>.parquet
```

Serialization is Apache Parquet (snappy-compressed), written with pyarrow. The exporter shipped gzipped NDJSON while the Pi ran 32-bit armv7l — pyarrow/DuckDB had no wheels for that platform — and decision 0003 chose a Parquet-shaped layout precisely so the 64-bit upgrade would swap only the serializer. With the Pi on 64-bit Raspberry Pi OS the serializer is now Parquet (objects written before the swap remain `.ndjson.gz`; DuckDB reads both). Each object is single-table, so its schema is uniform.

`samples` columns: `record_type`, `site_id`, `record_id` (sample sha256), `local_row_id` (int64), `captured_at`, `source`, `metric`, `value` (float64), `text`, `unit`, `tags` (map<string,string>).

`events` columns: `record_type`, `site_id`, `record_id` (event sha256), `local_row_id` (int64), `captured_at`, `source`, `event`, `detail` (JSON string — heterogeneous across event types, so it stays a string rather than a fixed struct).

`local_row_id` is diagnostic only. Consumers should use the content-hash `record_id` as the idempotency key.

## Timestamp Policy

Durable telemetry timestamps are stored as aware UTC ISO 8601 strings. Runtime readers should produce aware datetimes, the recorder normalizes writes to UTC, and displays convert to local time at the edge.

Historical rows written before 2026-06-18 may contain local-offset text such as `-04:00`. Readers and the exporter compare timestamps as instants with SQLite date functions, so mixed old/new rows remain queryable without an in-place migration.

Queries that mean "site-local day" should derive a local wall-clock window first, convert that window to UTC, and compare instants. Export partitions intentionally use the UTC capture date, not the local date.

## Ad Hoc Queries with DuckDB

Analysis runs on the Mac (or any 64-bit box), spanning the bucket and a synced copy of the local store in one session:

```sql
INSTALL httpfs; LOAD httpfs;
CREATE SECRET b2 (TYPE s3, KEY_ID '...', SECRET '...',
                  ENDPOINT 's3.<region>.backblazeb2.com');

-- archive in the bucket (partition-pruned by the date= path)
SELECT captured_at, value
FROM read_parquet('s3://<bucket>/metrics/samples/date=2026-06-*/*.parquet')
WHERE source = 'battery' AND metric = 'soc';

-- live store (scp blueberry.local:/srv/telemetry/data/metrics.sqlite first)
ATTACH 'metrics.sqlite' (TYPE sqlite);
SELECT captured_at, value FROM metrics.samples
WHERE source = 'battery' AND metric = 'soc';
```

The exporter is S3-generic: it accepts `B2_` or `S3_` prefixed variables (first match wins per `env_first`). **The live deployment uses Backblaze B2** (bucket `magpie-metrics`, region `us-east-005`); set `S3_*` instead to point it at any other S3-compatible provider.

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
