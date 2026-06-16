# Engineering Hardening Plan

Source: senior-review findings, 2026-06-09. Ordered by risk. Update status
markers as items land; move finished items to the Done section with the
commit reference.

Guiding observation from the review: the codebase structure is sound — the
risks are operational. Deploy discipline, reproducibility, and the gap
between "works on the bench today" and "rebuildable in a cold cabin in
February."

## 1. Single deploy action — DONE

One `scripts/deploy.sh` is the only deploy verb: `git pull --ff-only`,
sync configs to system locations, restart services, health-check.

The `offgrid-supervisor-restart.path` watcher is retired: it auto-restarted
the supervisor on every file change, so each rsync during bench iteration
caused a service restart (one of which killed the Kindle display on
2026-06-09). Explicit restart in deploy.sh replaces it.

rsync of individual files remains acceptable only for tight bench iteration,
and the session must end with `deploy.sh` so the Pi checkout returns to git
truth.

## 2. Truthful dependency manifest — DONE

`pyproject.toml` declared only `pymodbus` and `python-can`; the running
system also needs `magnum-pi` (which pulls pydantic and pyserial-asyncio).
Declared with pins. Rebuilding the venv from the repo on fresh hardware must
always work — restore-from-repo is a first-class scenario off-grid.

## 3. Tests for magnum.py packet identification — DONE

`tests/test_magnum.py` uses the exact hex captured live on 2026-06-09 —
the same bytes that exposed magnum-pi's CycleTracker misidentification.
Covers `_find_packets` in both packet orders, full `MagnumSnapshot` field
decode, and the 48V voltage scaling. Gate for any write-path work.

## 4. Stable serial device naming — DONE

udev `SYMLINK+="magnum-rs485"` for the SH-U11H (PL2303 067b:23a3);
supervisor uses `MAGNUM_DEVICE=/dev/magnum-rs485`. Prevents the wrong-bus
failure when a second RS485 adapter changes enumeration order.

## 5. CI — DONE

GitHub Actions workflow runs the unittest suite on every push. Catches
import breaks and contract regressions before code reaches hardware.
Requires item 2 (manifest) so `pip install -e .` brings real deps.

## 6. Per-device actor threads with staleness — PHASE 1 DONE

The supervisor poll loop read Classic (3s timeout), CAN (1.5s window), and
Magnum (serial open + up to ~5s) sequentially every 5s tick. One slow device
starved display freshness and, eventually, control decisions.

Phase 1 (done): `offgrid_power/readers.py` provides `PollingReader`, a
device actor — one thread owns each adapter exclusively. Reads happen on its
poll loop; writes are submitted to the same thread as queued commands
(`PollingReader.submit`), so reads and writes to one device can never race.
The charger taper writes via `Supervisor.write_classic_charge_settings`,
which routes through the classic actor. Failures keep the last good value;
stale readings (default 4x interval) surface as WARNING status conditions in
every display. Enabled by default; `--no-device-readers` restores the
synchronous path, and `--once` always reads synchronously.

Phase 2 (TODO):

- Persistent Magnum listener: hold the serial port open and consume bus
  cycles continuously instead of open/read/close per poll. Motivation is
  telemetry robustness: the open-per-poll pattern also makes ad-hoc bench
  captures collide with the supervisor on the serial port (hit 2026-06-10).
  (The remote-takeover transmit loop is no longer a driver — decision 0002
  was rejected; the ME-RC50 stays.)
- Surface per-device ages in the API payload (staleness is currently only a
  status condition).

## 7. SD card durability — SUPERSEDED by decision 0003

The SD-wear concern (sqlite every 60 s, CSVs rewritten whole every 5 min,
unbounded logs) is resolved by the 500 GB SSD + WAL + dropping the
rewrite-heavy CSVs. The telemetry storage redesign that replaces this item
is [decision 0003](decisions/0003-telemetry-storage-model.md).

## 8. Public-repo preparation — DONE

Placeholders/templates landed first; the history rewrite (username, paths,
coordinates, locator, author identity) and MIT license followed, and the
repo went public 2026-06-10. The journal entry for that date records the
sequence and the deploy.sh self-update lesson.

## 9. Operator actions — USER

- ~~`offgrid-metrics-export.timer` disabled — confirm export cadence~~
  **DONE:** the timer is enabled and runs daily at 12:05 to Backblaze B2.
  Export format is settled per decision 0003 — Parquet since the 64-bit
  migration (2026-06-13 OS, serializer swap 2026-06-16).

## 10. Combined charge-current coordination for parallel controllers — DEFERRED

When the Classic (array 0) and EPEver (array 1) both charge the bank at once,
the per-controller **current limit** stops being physically meaningful. Each
limit only caps its own source, so two 80 A caps allow up to 160 A into the
bank; the number that matters for protection (vs the Cubix's ~200 A CCL) and
for the LiFePO4 top-knee taper is the **combined** current. `charger_taper`
today computes one target and writes it to a single controller
(`--charger-current-taper-target`); if both arrays ever follow that schedule
independently, the combined current is ~2x the intended ramp.

Note the asymmetry: aligning the **voltage** setpoints (what
`scripts/epever-copy-from-classic.py` does) *is* correct and self-cooperative
— two CV sources regulating toward the same absorb voltage naturally share
and taper the holding current. Only the current-limit copy is the "solo-safe
default" rather than real coordination.

This is trickier than splitting the CCL 50/50. The two arrays have different
orientations and shading patterns, so their available output — and their
optimal contribution — diverge independently through the day; MPPT tracking
makes each instantaneous best-power point its own. A static per-controller
cap would throttle the array that could give more while the other can't fill
its share, wasting harvest. The likely shape is demand-aware: let each MPPT
harvest freely during bulk, and only constrain near the top knee, allocating
the shrinking combined budget toward whichever controller actually has the
power, driven by the **measured combined charge current** (sum the two
controllers' battery current, or use the BMS pack current) rather than any
fixed split.

Not urgent: only the Classic is a live charging source / taper authority
today, and the EPEver is in burn-in. This becomes real the moment both arrays
charge in parallel. Raised 2026-06-11.

## Done

- Item 1 (deploy.sh, watcher retired): e4444fc, a6f506f — verified with a
  live self-updating deploy.
- Item 2 (manifest): 936512a — verified by fresh `pip install -e .` on
  Python 3.12, 118 tests pass; suite now also runs on the Mac.
- Item 3 (magnum tests): 936512a — 9 tests from live-captured 2026-06-09
  packets.
- Item 4 (udev symlink): e04ba65 — `/dev/magnum-rs485` live, supervisor
  reading through it.
- Item 5 (CI workflow): 936512a — runs `unittest discover` on every push.
  First runs failed on a real bug: three load tests were
  timezone-dependent, green in Eastern and red on UTC runners. Fixed by
  pinning the site zone in the test module (19bc473); verified green via
  the now-authenticated `gh` CLI.
- Item 6 phase 1 (per-device actor threads): 36e9577 — one thread owns all
  I/O per device, writes queued onto the owning thread, last-good caching
  with staleness WARNING conditions. Snapshot composition went from
  worst-case ~10s of serialized device I/O to ~4ms cache reads, verified
  live. Phase 2 (persistent Magnum listener, per-device ages in the API)
  remains above.
