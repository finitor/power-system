# 0003: One Flat Time-Series Store, Parquet Export, DuckDB Query Layer

Date: 2026-06-11

## Status

Accepted — implementation phased (gated on the SSD landing on a powered adapter)

## Context

Logging had grown three overlapping models (review 2026-06-11):

- `supervisor_snapshots` — full JSON API-payload blob every 60 s. The live
  primary store; no retention; ~the only populated telemetry table.
- `metric_samples` — a flat EAV time-series (sha256 dedup, export columns,
  indexes, views) — fully built and unit-tested but **never wired to the
  production write path** (0 rows).
- CSV side-files (`load-samples`, `charger-taper`, `inverter-events`,
  `load-soc-baselines`) duplicating data already in the snapshot JSON, plus
  `web-display-access.log` (the largest writer, no consumer).

Nothing consumes any of it yet, so all of it is open to redesign. The goal
is ad hoc queries and charting over **both** the local store and the R2
archive. A 500 GB SSD (Samsung 840 EVO) is being added, which removes the
SD-card wear/capacity constraints that shaped the aggressive,
multi-model design.

## Decision

One canonical local model: the **flat `metric_samples` EAV time-series**,
plus a small **`events`** table for irregular events. (The table was
renamed to **`samples`** on 2026-06-12, before any consumers existed —
shorter to type in ad hoc SQL forever, and symmetric with `events`.
References to `metric_samples` below describe the same table.)

- **Scalars** (voltages, currents, SOC, temps, charge settings, …) →
  `metric_samples` rows. Adding a metric (new sensor, EPEver register,
  per-cell RS485 data) needs **no schema change** — just new `metric`
  names. `tags_json` carries per-sample dimensions (device id, cell index)
  without migration. This preserves the schema flexibility that JSON
  snapshots offered; only nested-structure/bit-exact replay is given up,
  which scalar telemetry does not need.
- **Events** (taper decisions, inverter on/off, LBCO cut-outs) → `events`
  table with a small `detail_json` column (the one place a flexible blob
  earns its keep).
- **Retire**: `supervisor_snapshots` JSON blobs, all the telemetry CSVs,
  and `web-display-access.log`. Displays are unaffected — they read the
  in-memory snapshot cache, never the store.
- **R2 export**: ship **date-partitioned Parquet** (columnar, queryable in
  place) rather than gzipped NDJSON.
- **Query layer**: **DuckDB** as the one engine that spans the local SQLite
  and R2 object storage in a single query; optionally Grafana on the local
  SQLite for live dashboards.
- **SQLite**: WAL mode, `noatime`; `/srv/telemetry` mounted on the SSD via the
  existing `srv-telemetry.mount` unit; retention generous (keep raw at full
  resolution for years — trivial on 500 GB).

## Benchmarks (2026-06-11, Apricorn SATAWire bridge on the powered hub)

Samsung 840 EVO 500 GB, reformatted GPT/ext4 `noatime`, mounted scratch at
`/mnt/ssd-test`:

| Test | Result |
|---|---|
| Aged-data read (old APFS blocks, `direct`) | 8.8 MB/s — the 840 EVO stale-cell slowdown |
| Fresh sequential write (1 GB, fsync) | 36.8 MB/s — Pi 3B+ USB 2.0 ceiling |
| Fresh read-back (cache dropped) | 36.9 MB/s — confirms the slow read was aged cells |
| SQLite WAL, 2000 fsync'd inserts | 0.05 s |

Bridge bound to `usb-storage` (BOT), not UAS — no UAS reset quirk. No TRIM
(`discard_max_bytes=0`); SMART not exposed. Throughput is ~3 orders of
magnitude over what a log store needs; the drive is more than adequate.

## Posture: integrity is out of scope for now (operator decision 2026-06-11)

Log data is non-critical and will be for a long time. So **build the
production-like store on the SSD now** — do not wait for the dedicated
adapter on integrity grounds. The accepted risk: an abrupt power loss
through the BOT bridge (unknown flush honesty) could corrupt the SQLite
store; worst case we discard and recreate it. That is fine because:

- **Logging is best-effort and strictly isolated from the supervisor.** A
  corrupt/missing/unwritable store must never disrupt telemetry, displays,
  or control — the recorder catches all errors, recreates the DB if it
  fails to open, and the live path runs from the in-memory snapshot cache
  regardless. This isolation is the hard requirement that makes
  "don't care about integrity" safe.
- Nothing consumes the log yet, so loss is recoverable by definition.

The dedicated adapter and a power-loss test remain wanted *eventually* (when
log data starts mattering), but they no longer gate hosting the live store.

## Consequences

- Removes the three-way redundancy; one analysis-ready substrate turns the
  taper dry-run log and inverter-events into queryable history instead of
  write-only files.
- `snapshot_metric_samples()` (already built + tested) becomes the live
  write path; the JSON-snapshot writer and CSV writers are deleted.
- R2 export re-points to the canonical tables (their export columns already
  exist) and changes serialization to Parquet — adds a Parquet writer
  dependency (e.g. pyarrow) on the export side.
- **Hardware constraint:** the SSD's ~2-3 W must not come off the Pi's
  shared USB rail — that draw is the likely trigger of the 5 V sag that
  wedges the CAN adapter. It is fine on externally-supplied power: the
  dedicated powered SATA adapter (pending), OR a bus-powered adapter
  plugged into the powered Waveshare hub (the hub feeds it from its own
  PSU), subject to that hub's spare headroom. Prototyping via the hub is
  acceptable; see the prototyping caveats below before trusting it with the
  live store.
- Supersedes engineering-plan item 7 (SD-card durability): the SSD plus WAL
  plus dropping the rewrite-heavy CSVs resolves it.

## Removable SSD with SD fallback and merge-on-reattach

Wanted: log to the SSD normally; if it is removed, fall back to the SD card
so nothing is lost; when it returns, sync the gap back to the SSD and
resume. This is **more practical than it looks, because of the flat model's
content-hashed `sample_id` (UNIQUE index)** — the same machinery that
looked like over-engineering. Design:

- Each row's identity is `sample_id = sha256(content)`, so merging two
  stores is an **idempotent union**: `ATTACH` both DBs and
  `INSERT OR IGNORE INTO main.metric_samples SELECT * FROM other...`.
  Order-independent, no "what did I miss" bookkeeping, safe to re-run. The
  `events` table needs the same hash-key treatment to participate.
- Write target = SSD if its mountpoint is live, else the SD store. Detect
  via the mountpoint (a `systemd .automount`, or a check in the recorder).
- On reattach: merge SD → SSD (idempotent union), then switch writes back
  to SSD. Optionally prune the SD store's merged rows afterward.
- The SD fallback store is small and short-lived (only the removal gap), so
  SD wear stays modest under the append-only SQLite model.

This rides directly on the best-effort logging isolation above: target
switching and merge failures degrade logging only, never the supervisor.
Build it as part of the storage work rather than bolting it on later.

## Prototyping caveats (now accepted risks, not blockers)

With integrity out of scope (above), these are documented characteristics
of the current bridge rather than reasons to wait:

- **UAS resets:** not a concern — the Apricorn bound to `usb-storage`
  (BOT), confirmed at enumeration. (Were a future bridge to bind UAS and
  misbehave, blacklist it via a `usb-storage.quirks=...:u` kernel param.)
- **No TRIM/SMART** through this bridge — fine for a log store; no wear
  telemetry, and rely on the 840 EVO's own GC plus occasional rewrite.
- **Power-loss flush integrity unknown** — accepted risk per the posture
  above; a power-cycle integrity test is deferred until log data matters.
- **Aged-data slowdown** is intrinsic to the 840 EVO; a periodic full
  rewrite (or just generous free space) keeps reads fast. Irrelevant to
  the write-mostly log workload.

## Phased implementation

Integrity is out of scope, so this builds to the live store directly (no
prototype-only holdback). The SSD currently runs on the Apricorn bridge via
the powered hub; the dedicated adapter swaps in later without code changes.

**Current state (2026-06-11):** the path rename to `/srv/telemetry` is done,
but the live store still sits on the **SD card** — the fstab entry there
references the *old removed* SSD's UUID (`8d7214f5…`, absent, `nofail`), so
`/srv/telemetry` is just a directory on the SD root. The new SSD
(`8e8a4fee…`) is formatted and mounted at `/mnt/ssd-test`. Deliberate
decision: **leave the store on SD until phase 1 work begins** — no point
cutting the live store onto the prototype-bridge SSD before any storage code
changes. Phase 1 performs the cutover.

1. **SSD cutover (do this when phase work starts, not before):** unmount
   `/mnt/ssd-test`; repoint the fstab entry from the old UUID `8d7214f5…` to
   the new SSD `8e8a4fee…` at `/srv/telemetry`; move the existing store
   (`/srv/telemetry/{data,logs}`) onto the SSD; mount it. ext4 `noatime`,
   SQLite WAL + `synchronous=NORMAL`. (The `srv-telemetry.mount` generated
   unit follows the fstab change.)
2. ~~Best-effort recorder~~ **Done 2026-06-12:** all store writes catch every
   failure; a store that fails to open/write is moved aside
   (`*.corrupt-<ts>`) and recreated; reads degrade to "no data".
3. ~~Wire the flat path~~ **Done 2026-06-12:** `snapshot_metric_samples()` is
   the live write path (now also covering EPEver and Magnum sources, which
   the JSON-blob era missed); hash-keyed `events` added; taper decisions and
   inverter on/off events write to `events` (the inverter tracker was
   previously built but never wired); the load rolling buffer is in-memory,
   seeded back from the store at startup; the midnight-SOC baseline is read
   from the store.
4. ~~Dual-store with merge-on-reattach~~ **Done 2026-06-12:** the recorder
   takes `mountpoint=/srv/telemetry` and
   `fallback_path=/var/lib/offgrid/metrics-fallback.sqlite` (SD). Writes use
   the primary only while the mountpoint is actually mounted (guarding the
   shadowed-directory trap) and also fall back when a primary write fails —
   a yanked USB device raises `OperationalError`, which falls back rather
   than triggering the corrupt-store discard (that is reserved for
   corruption-shaped `DatabaseError`s). After the next successful primary
   write, the fallback is unioned back (content-hash `INSERT OR IGNORE`)
   and deleted; merge failures just retry next tick. Reads follow the
   active store.
5. ~~Drop legacy writers~~ **Done 2026-06-12, ahead of order:** the
   `supervisor_snapshots`/`device_settings_snapshots`/`weather_snapshots`
   writers, all telemetry CSVs (load-samples, load-soc-baselines, ambient,
   charger-taper, inverter-events), and `web-display-access.log` are deleted
   outright — operator accepted a no-logging transition window rather than
   dual-running.
6. ~~R2 export → date-partitioned Parquet~~ **Done 2026-06-12, amended:
   date-partitioned gzipped NDJSON instead of Parquet.** The Pi runs
   32-bit Raspbian (armv7l) and pyarrow ships no armv7l wheels (piwheels
   included; verified by a failed `--only-binary` install), so Parquet
   cannot be written on this host. The goal — one query engine spanning
   local + object storage — survives the substitution: DuckDB reads
   `.ndjson.gz` from S3 natively, and the exporter writes the
   Parquet-style *layout* (per-table objects under hive partitions,
   `metrics/{samples,events}/date=YYYY-MM-DD/<ts>-<batch>.ndjson.gz`,
   uniform schema per object, oldest date drains first), so a future
   64-bit OS upgrade swaps only the serializer. At this data volume
   (a few MB/day gzipped) the columnar efficiency loss is irrelevant.
   `offgrid-metrics-export.timer` re-enabled (daily 12:05). DuckDB
   itself also has no armv7l wheel — ad hoc analysis runs on the Mac
   (or any 64-bit box), querying B2 plus a synced copy of the SQLite
   store.
7. ~~Backfill optional~~ **Resolved 2026-06-12 — discarded.** Operator chose
   simplicity over history: the legacy tables were dropped from the live DB
   (14.4 MB → 416 KB after VACUUM) and the retired CSVs/access log deleted
   from the SSD, the SD shadow copy, and the repo-local `data/` dir. The
   flat store's first samples (2026-06-12) are the start of history.
