# Querying the Telemetry Log

The supervisor writes all readings to `/srv/telemetry/data/metrics.sqlite` once per minute. The `scripts/query_metrics.py` helper covers the most common questions. For anything else, open the DB directly with Python.

## Common questions

### What was peak charging power today?

```
python3 scripts/query_metrics.py peak-power
python3 scripts/query_metrics.py peak-power --date 2026-06-14
```

### What's today's energy total?

```
python3 scripts/query_metrics.py daily-summary
```

Prints energy (kWh + Ah), peak charge power and when it occurred, peak PV voltage, and last open-circuit voltage.

### What is the system doing right now?

```
python3 scripts/query_metrics.py now
```

Latest reading for every key metric across the Classic, battery pack, and CAN bus.

### When did the charge stage change today?

```
python3 scripts/query_metrics.py charge-history
```

Lists each Bulk → Absorb → Float transition with the time it occurred.

---

## Interactive query shell

For pure SQL exploration, the `sqlite3` CLI is the most direct interface.

The database is owned by the `offgrid` user. Add yourself to that group once so plain paths work:

```
sudo usermod -aG offgrid $USER   # then log out and back in
```

Even after that, prefer the read-only URI for inspection:

```
sqlite3 'file:/srv/telemetry/data/metrics.sqlite?mode=ro'
```

An ordinary writable open creates or replaces SQLite WAL/SHM sidecars. The
telemetry directory now has setgid and a default group-writable ACL so those
sidecars remain usable by the `offgrid` service, but read-only mode avoids the
interaction entirely. Use a writable session only for an intentional database
maintenance operation, and run it as the service account:

```
sudo -u offgrid sqlite3 /srv/telemetry/data/metrics.sqlite
```

Do **not** use `immutable=1` on this database: it tells SQLite the file cannot
change, but the supervisor writes once a minute (WAL mode), so long scans fail
midway with spurious `database disk image is malformed` errors. `mode=ro`
takes proper read locks and stays consistent against the live writer.

Useful dot-commands:

```sql
.headers on
.mode column
.tables
PRAGMA table_info(samples);
```

When you need to further process a result set — reshape it, feed it into a plot, compute things SQL can't express cleanly — use the Python shell instead:

```
python3 scripts/query_metrics.py shell
```

Drops into a Python REPL with `conn` (`sqlite3.Connection`, `row_factory=Row`) pre-loaded:

```python
rows = conn.execute("SELECT captured_at, value FROM samples WHERE metric = 'battery_power' ORDER BY captured_at DESC LIMIT 5").fetchall()
dict(rows[0])   # {'captured_at': '2026-06-15T10:07:12...', 'value': 1620.0, ...}
```

`sqlite3` is also in scope. Exit with `Ctrl-D`.

## Direct SQL queries

Open read-only without copying the file:

```python
import sqlite3
conn = sqlite3.connect('file:/srv/telemetry/data/metrics.sqlite?mode=ro', uri=True)
```

### Schema quick reference

Table: `samples`

| column | notes |
|---|---|
| `captured_at` | ISO-8601 in **UTC**, e.g. `2026-06-15T14:07:12.284562+00:00` (always `+00:00`) |
| `source` | `classic.0`, `epever.1`, `battery`, `battery.can`, `load`, `tasmota.<name>`, `weather`, `supervisor` |
| `metric` | metric name (see below) |
| `value` | numeric |
| `text` | string (for stage names, states, etc.) |
| `unit` | `W`, `A`, `V`, `kWh`, `Ah`, `C`, … |

**Date filtering:** `captured_at` is stored in UTC. Because the text is uniform UTC, lexical (string) comparison equals chronological order, and SQLite's `date()`/`datetime()` agree with the raw text — both report the **UTC** day.

That UTC day is *not* the local calendar day. At this site (UTC−04:00 in summer) local midnight is `04:00` UTC, so a bare prefix like `captured_at >= '2026-06-15'` selects the UTC day, shifted ~4h from the local day. To filter by **local** day, compare against the local-midnight boundaries expressed in UTC:

```sql
-- "2026-06-15" local, at UTC-04:00:
WHERE captured_at >= '2026-06-15T04:00:00+00:00'
  AND captured_at <  '2026-06-16T04:00:00+00:00'
```

`scripts/query_metrics.py` computes these bounds for you (see `day_bounds`, which mirrors `offgrid_power.metrics.local_day_utc_bounds`), so the subcommands below report local-day results and display times in local time.

### Key metrics — `classic.0`

| metric | unit | notes |
|---|---|---|
| `battery_power` | W | charging power delivered to battery |
| `battery_current` | A | |
| `battery_voltage` | V | |
| `pv_voltage` | V | panel string input |
| `pv_current` | A | |
| `charge_stage` | — | text: Bulk, Absorb, Float, EQ, … |
| `daily_energy` | kWh | running daily total, resets at midnight |
| `daily_amp_hours` | Ah | running daily total |
| `last_voc` | V | last measured open-circuit voltage |
| `highest_input_voltage` | V | daily peak PV voltage |
| `fet_temperature` | C | MOSFET temperature |
| `pcb_temperature` | C | |

### Key metrics — `epever.1`

Mostly parallel to `classic.0` (`battery_power`, `pv_voltage`, `pv_power`, …)
with one trap: **`generated_today` is a cumulative counter, not a daily
total** — it climbs across days and resets unpredictably (device restarts),
so `MAX(value)` per day does not give daily energy. Integrate `battery_power`
over time instead.

### Key metrics — `battery` / `battery.can`

| metric | unit | notes |
|---|---|---|
| `voltage` | V | pack voltage |
| `current` | A | positive = charging |
| `soc` | % | state of charge |
| `temperature` | C | |

### Key metrics — `tasmota.<name>`

Each configured S31 has a stable source such as `tasmota.refrigeration`.
Metrics are `voltage`, `current`, `power`, `apparent_power`, `reactive_power`,
`power_factor`, `daily_energy`, `yesterday_energy`, and `lifetime_energy`.
The device owns its energy counters; `daily_energy` follows the S31's configured
local clock and resets at its local midnight.

### Example: peak charge power on a specific day

```python
conn.execute("""
    SELECT captured_at, value, unit
    FROM samples
    WHERE source = 'classic.0'
      AND metric = 'battery_power'
      AND captured_at >= '2026-06-15'
      AND captured_at < '2026-06-16'
    ORDER BY value DESC
    LIMIT 1
""").fetchone()
```

### Example: daily energy totals for the past week

`daily_energy` is a running counter that the Classic resets at **local** midnight, so bucket by the local day. SQLite's `date()` would group by the UTC day (~4h off); shift into local time first with the site offset:

```python
conn.execute("""
    SELECT date(captured_at, '-4 hours') AS local_day, MAX(value) AS kwh
    FROM samples
    WHERE source = 'classic.0'
      AND metric = 'daily_energy'
    GROUP BY local_day
    ORDER BY local_day DESC
    LIMIT 7
""").fetchall()
```

(`MAX` because the counter peaks at end-of-day. The `-4 hours` is the summer offset; for a DST-correct, offset-free version use `scripts/query_metrics.py daily-summary --date …` per day, which derives the local-day bounds from the system zone.)
