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
plus a small **`events`** table for irregular events.

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
- **SQLite**: WAL mode, `noatime`; `/srv/offgrid` mounted on the SSD via the
  existing `srv-offgrid.mount` unit; retention generous (keep raw at full
  resolution for years — trivial on 500 GB).

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

## Prototyping caveats (bus-powered adapter on the powered hub)

The cheap SATA bridge is fine to validate the *software* path, but do not
migrate the live `/srv/offgrid` onto it until proven. Three reasons,
beyond hub PSU headroom:

- **UAS resets.** Cheap JMicron/ASMedia bridges often misbehave under the
  UAS driver on a Pi (link resets, mid-write drops). Watch `dmesg` for
  `usb ... reset` / `uas` errors over a day; if seen, blacklist UAS for
  that VID:PID via a `usb-storage.quirks=...:u` kernel param.
- **No TRIM/SMART** likely passes through a cheap bridge — fine for a log
  store, but no wear telemetry.
- **Power-loss flush integrity unknown.** A bridge that lies about FUA/flush
  can corrupt SQLite on sudden power loss (BMS cutoff, etc.) even with WAL.
  This is the specific reason to wait for the dedicated adapter before the
  store is production — and to bench a power-cycle test first.

Safe prototype path: mount at a scratch path (e.g. `/mnt/ssd-test`), run a
*parallel* metrics DB or a soak/benchmark there, survive a deliberate
power-cycle, and only then consider hosting the live `/srv/offgrid`.

## Phased implementation

1. SSD on a powered adapter (or the powered hub for prototyping per above);
   `/srv/offgrid` mounted on it; SQLite WAL.
2. Wire `snapshot_metric_samples()` into the live recorder; add the
   `events` table; migrate taper/inverter/load writers off CSV.
3. Drop `web-display-access.log`; drop the `supervisor_snapshots` writer
   once the flat store is confirmed.
4. R2 export → date-partitioned Parquet; verify DuckDB can query local +
   R2 uniformly.
5. Backfill is optional — existing snapshot JSON can be flattened into
   `metric_samples` once if the history is worth keeping, else discard.
