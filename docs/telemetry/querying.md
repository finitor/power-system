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

After that:

```
sqlite3 /srv/telemetry/data/metrics.sqlite
```

Until then (or to avoid accidental writes regardless), use the immutable URI:

```
sqlite3 'file:/srv/telemetry/data/metrics.sqlite?immutable=1'
```

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
conn = sqlite3.connect('file:/srv/telemetry/data/metrics.sqlite?immutable=1', uri=True)
```

### Schema quick reference

Table: `samples`

| column | notes |
|---|---|
| `captured_at` | ISO-8601 with local TZ offset, e.g. `2026-06-15T10:07:12-04:00` |
| `source` | `classic.0`, `epever.1`, `battery`, `battery.can`, `load`, `weather`, `supervisor` |
| `metric` | metric name (see below) |
| `value` | numeric |
| `text` | string (for stage names, states, etc.) |
| `unit` | `W`, `A`, `V`, `kWh`, `Ah`, `C`, … |

**Date filtering:** `captured_at` stores local time with a TZ offset. Filter by string prefix — `captured_at >= '2026-06-15'` — rather than SQLite's `date()` function, which interprets values as UTC.

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

### Key metrics — `battery` / `battery.can`

| metric | unit | notes |
|---|---|---|
| `voltage` | V | pack voltage |
| `current` | A | positive = charging |
| `soc` | % | state of charge |
| `temperature` | C | |

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

```python
conn.execute("""
    SELECT date(captured_at) AS day, MAX(value) AS kwh
    FROM samples
    WHERE source = 'classic.0'
      AND metric = 'daily_energy'
    GROUP BY day
    ORDER BY day DESC
    LIMIT 7
""").fetchall()
```

(Uses `MAX` because `daily_energy` is a running counter that peaks at end-of-day.)
