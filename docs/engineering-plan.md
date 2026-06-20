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

## 10. Combined charge-current coordination for parallel controllers — DONE

**DONE (live 2026-06-18):** superseded by the closed-loop charge allocator
([Real-Time Charge Current Allocation](charge-current-allocation.md)), which
replaced the single-controller `charger_taper`. The allocator resolves one net
charge budget from BMS CCL (engaging at the BMS taper knee) and apportions it
evenly across both controllers, driven by measured combined/BMS current rather
than a fixed split — exactly the demand-aware shape sketched below. Both arrays
now charge in parallel under one authority; the EPEver is a committed, in-service
controller (no longer in burn-in). The original framing is kept below for
context.

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

## 11. Load accounting: incorporate the Magnum charger — DEFERRED

The Load group's instantaneous and cumulative figures balance the DC bus over
the two solar producers (Classic + EPEver) plus the BMS net current. The
Magnum is the third energy port and is **not** in that balance:

- **Instantaneous** (`estimate_load_current_a`): while the Magnum is
  *inverting*, its DC draw is already captured implicitly by the BMS net
  current, so it correctly cancels. The gap is only when the **generator runs
  and the Magnum charges** — then it is a source that should be added to
  charge-in, or load reads low.
- **Cumulative** (`estimate_load_today_kwh`): should add the Magnum's daily
  **charger** energy to the producer sum, the same way Classic/EPEver daily
  kWh are summed.

The blocker is **measurement access**, and we are unsure we have it: we read a
passive tap of the inverter packet (`MagnumSnapshot`), which gives instantaneous
`dc_amps`/`dc_power_w` but **no cumulative energy counter**, and its DC-amp sign
under charge is **unverified** (it reads positive while inverting; the
charge-direction sign has never been observed). So before this can land we need
to, on a *running generator*: (a) confirm the `dc_amps` sign while charging,
(b) isolate the pure charger contribution from inverter draw (the
`charger_on` flag helps), and (c) since there is no energy register, integrate
the charger power ourselves into a daily total (anchored to local midnight, like
the EPEver derivation). If the tap can't cleanly separate charger output, this
may not be feasible without a different Magnum data source (BMK/ME-RC).

Low priority: generator charging is human-attended and infrequent, so the Load
figures are only wrong during those windows. Raised 2026-06-17.

## 12. Magnum RS485 read failures since EPEver PV connection — OPEN (physical)

Magnum "no valid inverter packet seen" read failures rose sharply starting
**exactly when the EPEver array was connected to PV** (2026-06-16), and have
persisted since. The supervisor serves last-good telemetry through them, so it's
WARNING-flapping and gappy reads, not lost state — but it's real and new.

Ruled out — **not** correlated with EPEver *charging output*. A full day of data
(journal failure timestamps vs stored EPEver current/PV power, by hour):
failures are present around the clock and are actually **highest overnight when
the EPEver is fully idle (0 W)** and not elevated during peak charging
(217 W → 44/hr vs 0 W overnight → 50-67/hr). 85% of failures occur with the
EPEver at <=1 A. So it is **not** charging-current/switching noise.

But the onset is tied to the *physical* PV attachment, so the likely cause is
electrical/coupling from the array wiring or grounding regardless of current
(the strings/feeders as an antenna or a shared-ground path), or simply that
recabling for the EPEver disturbed the Magnum RS485 run/adapter. Avenues to try
(operator, on site): reroute/separate the Magnum RS485 cabling from the PV
runs, check grounding, and/or swap the Magnum USB-RS485 adapter (the SH-U11H).

Mitigation already in place: `MagnumClient.max_cycles` raised 10 -> 20 so a read
tolerates more missed cycles before warning (quiets the noise; not a fix).
Raised 2026-06-17.

Diagnosis refined (2026-06-17) -- byte-framing slips, one root cause for both log
signatures. Alongside the `no valid inverter packet` warnings (~14-16/hr) there
are `Failed to parse REMOTE packet ... less_than_equal` errors (~3-4/hr). Decoded
against the known-good fixture, the failing remote payload is the good packet
with its leading `0x00` dropped: every field shifts left one, so
`battery_size_ah`'s 100 lands in `search_watts` (limit 50) and trips validation.
So it's a lost byte on the wire (signal integrity) -- not charging noise, not
value corruption, and not a parser bug (the `le=` limits correctly reject the
misframe; do not loosen). Both signatures are bus byte/cycle-sync loss,
consistent with the physical-onset theory. Byte-alignment proof in
`research/magnum-inverter-interface.md`.

Grounding experiment (2026-06-17 ~20:50): added the RS485 signal-ground reference
the Magnum tap lacked -- breakout pin 5 (Magnum GND) -> SH-U11H GND via a 150 ohm
series resistor (measured 0.58 V offset -> ~3.9 mA, no ground-loop risk; pin 8
read 0 V = floating, not ground). Baseline before: ~14-16/hr "no inverter packet"
+ ~3-4/hr parse fails. Evaluate after it runs an hour:
`journalctl --since "<HH:MM>" --no-pager | grep -c "no valid inverter packet"`
(and the `Failed to parse REMOTE` string). A drop in either confirms common-mode
noise was a contributor; no change means the framing loss is bus-timing/wiring,
not noise.

Result (2026-06-18, first overnight, matched hours to control for the
overnight-is-noisier pattern): "no valid inverter packet" 51.9/hr -> 36.1/hr
(~30% drop), "Failed to parse REMOTE" 6.3/hr -> 5.2/hr (~18% drop)
(pre = Jun16 21:00-Jun17 06:30, post = Jun17 21:00-Jun18 06:30, 9.5h each). Both
fell in the same direction -> the ground reference is a contributor; keep it.
Caveats: single night-pair (n=1, some could be night-to-night variance), and the
errors are far from eliminated (36/hr residual) so the bus is still marginal.
Next levers: the tap's `120R` termination jumpers are unpopulated -- RS485
termination is the likely highest-yield next experiment (reflections cause this
same byte loss); then reroute the RS485 away from the PV runs / swap the adapter.

## 13. Operator live-tuning controls — DONE

Live, on-console operator control of the two knobs that shape charging near the
taper knee, added 2026-06-20 (0a7eb3a, 90b684b, 342a1a1, c0c52cf).

- **Scalar charge voltage** per controller: `delta_v` on the scalar-voltage API
  (read-modify-write + readback confirm), `scripts/charge-controller-voltage.py
  --by`, and a staged-commit **tune mode** (`t`) in the terminal display.
- **CCL scaling factor** (the allocator's fraction of BMS CCL near the knee;
  renamed from "budget fraction" to free up "budget" for limit+load): same API /
  script / tune-row treatment, persisted across restarts in
  `/var/lib/offgrid/runtime-state.json` (env default applies only when absent).
- Guard rails: staged-commit (explicit Enter), session budget, idle auto-disarm,
  BMS-CVL backstop, per-call delta caps. Default scaling step is 10 points so a
  nudge clears the allocator's 5 A deadband; the Limit line surfaces the live
  factor. See [docs/journal/2026-06-20.md](journal/2026-06-20.md) and
  [charge-current-allocation.md](charge-current-allocation.md).

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
