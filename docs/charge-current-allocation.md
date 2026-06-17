# Real-Time Charge Current Allocation

The BMS charge-current limit (CCL) is a **net battery charge-current** limit.
The Classic and EPEver current settings are **charger output-current** limits.
Those are not the same quantity when the house is consuming power from the DC
bus. The supervisor should therefore own a system-level charge budget:

```text
allowed charger output ~= BMS CCL + household load - reserve
```

The invariant is:

```text
net battery charge current <= BMS CCL
```

Controller limits are only the actuators used to maintain that invariant.

## Algorithm (as implemented)

Once per supervisor cycle the allocation logger builds a `ChargerAllocationInput`
for each controller from one coherent snapshot, evaluates the battery-state
ceiling, calls the pure allocator `decide()`, logs the decision (on material
change + a heartbeat), and — when live — writes the per-controller limits.
`decide()` is a pure function; the only state (the full-charge latch) lives in
the ceiling object. It resolves the per-cycle targets in this order:

1. **Missing data → no action.** If BMS CCL or pack current is unreadable,
   return no targets and write nothing (fail safe; never guess).
2. **Charge disabled / CCL ≤ 0 → stop.** If the BMS charge-enable flag is false
   (live: also if it is *unreadable*) or CCL ≤ 0, command every charger off —
   Classic 0 A, EPEver coil off.
3. **Battery-state ceiling.** Evaluate `ChargeCeiling` (top-knee taper + cell
   safety, below): a cap on *total net charge current*. The effective limit is
   `min(BMS CCL, ceiling)`. If that is ≤ 0 (a cell-safety stop or the full-charge
   latch), stop all chargers, carrying the ceiling's reason.
4. **Unconstrained short-circuit.** If every eligible charger at its own max
   still couldn't reach the effective limit — `Σ(max) ≤ effective CCL` — the
   allocator is *not* the binding constraint (sunlight is). Pin each charger to
   its own max: no reserve, no apportionment, no per-cycle writes. This is the
   normal state for most of a sunny day.
5. **Budget.** Otherwise `budget = effective_CCL + max(load, 0) − reserve`,
   then the feedback clamp (see Budget).
6. **Apportion** the budget across eligible controllers by PV-power weight
   (water-fill with cap redistribution).
7. **Targets → writes.** Each target is capped at the controller's max; a target
   below its `min_current_a` floor becomes `disable`. Live writes are gated by a
   deadband (`min_write_delta_a`): the Classic limit is written volatile, the
   EPEver current register is written, and the EPEver charge coil is toggled to
   match the disable intent (only on change).

The binding limit — `unconstrained`, `normal_load_allowance`, `feedback_clamp`,
the ceiling reason (`top-knee taper`, `full-charge latch`, a cell stop), or a
disable reason — is recorded as the decision `reason`, so a trace shows *why*
each cycle did what it did.

### Battery-state ceiling

`ChargeCeiling.evaluate()` caps total net charge current from battery state and
is combined with the BMS CCL by `min()`:

- **Top-knee taper:** a ceiling that ramps down with SOC and with pack voltage
  (the lower wins). Below the knee → no ceiling (CCL/budget govern).
- **Cell-safety stops → 0 A:** max cell ≥ `high_cell_stop_v`, or cell delta ≥
  `high_delta_stop_mv` while max cell ≥ `high_cell_soft_limit_v`.
- **Full-charge latch → 0 A:** once SOC reaches `full_soc_percent`, hold zero
  until the pack rests (SOC < `full_reset_soc_percent` **and** voltage ≤
  `full_reset_voltage_v`). This is the one piece of carried state.

> ⚠️ The ceiling thresholds are inherited from the single-controller ~54 V-era
> taper and **over-clamp at the bank's current 55–56 V operating point** (voltage
> above `top_voltage_v` pins the ceiling to `ramp2_low_current_a` ≈ 4 A). They
> are env-tunable (Operator controls) and should be re-tuned once a full charge
> cycle of traces is in hand.

## Inputs

Use one coherent supervisor snapshot per decision:

- BMS CCL, charge-enable flag, pack current, cell voltage/delta alarms.
- Classic actual output current, present charge-current limit, stage/state.
- EPEver actual output current, present charge-current limit, stage/state.
- Estimated household load current.

The existing load estimator already uses the right bus balance:

```text
load current = Classic output + EPEver output - BMS net battery current
```

If load is unavailable, the allocator should fall back conservatively and
prefer undercharging to exceeding CCL.

## Budget

The feed-forward budget is:

```text
budget_a = max(0, bms_ccl_a + max(load_a, 0) - reserve_a)
```

Then close the loop with BMS current:

```text
if measured_battery_charge_a > bms_ccl_a + tolerance_a:
    budget_a -= measured_battery_charge_a - bms_ccl_a
```

The feedback clamp catches bad load estimates, stale controller output, and
controller overshoot.

The clamp is a unity-gain proportional correction, and `reserve_a` is its
companion margin. If the load estimate is biased high by more than the reserve,
the feed-forward over-allocates and the clamp pulls net battery current back down
to exactly CCL in steady state — safe, just at the ceiling. So `reserve_a`
absorbs load-estimate error in the safe direction and the clamp catches the
unsafe direction; the clamp's "down" is effectively immediate (it's recomputed
every cycle from the measured current).

**No rate-limiting on the limit writes.** A controller's current limit is a
*ceiling*, not a setpoint: the controller still ramps its actual output via its
own CV regulation / soft-start, so jumping the limit does not surge current.
That, plus the budget-level clamp giving immediate-down behaviour, is why we do
not slow the limit writes — and not slowing them also avoids extra EEPROM writes
to the EPEver. If traces ever show oscillation, revisit this.

## Apportionment

Eligible controllers receive a share of the budget by water-filling: weights set
the proportional split, each target is capped at the controller's
configured/operator maximum, and budget unused by a capped controller is
redistributed to the rest. This naturally gives the larger or sunnier array more
budget without hardcoding array sizes, while still letting the other participate.

- Online, enabled, and *able-to-charge* controllers are eligible (see below).
- Unavailable controllers get no target.

### Weighting signal

The weight must reflect how much each array *could* deliver, not what it is
currently delivering. Weighting by present output is a latch: throttle a
controller down → its output falls → its weight falls → it stays throttled, even
if its array now has the most sun. And this bites precisely when the budget is
binding, which is the only time apportionment matters — when budget-bound, every
controller sits at the target we set, so their outputs reflect our allocation,
not their potential.

So weight by **PV input power** (`pv_power_w`), which is independent of the limit
we wrote. Fall back to actual output current only when PV power is unavailable,
and pick one basis for the whole eligible set per decision — mixing watts and
amps corrupts the ratios. A small floor on each weight keeps a not-yet-producing
controller from being starved at sunrise. The chosen basis is recorded in the
decision (`weight_basis`) so traces show which signal drove each split.

### Eligibility

"Able to charge" means PV present / not faulted — **not** "currently sourcing
current." The supervisor must not derive eligibility from present output, or
throttling a controller toward zero would drop it from the eligible set and it
could never be handed budget back.

## Safety States

- **Disabled:** BMS charge-enable false or CCL <= 0. Command active chargers to
  zero/off.
- **Conservative:** BMS CCL or pack current missing → no targets, no writes (the
  implemented default; safe). A "small fallback charge while stale" variant is a
  future option handled upstream by passing a conservative CCL into the
  allocator, not by the allocator guessing.
- **Normal:** Allocate `CCL + load - reserve`.
- **Clamp:** If measured net battery current exceeds CCL, subtract the excess
  immediately.
- **Recovery:** Restore headroom slowly after the clamp clears.

EPEver's current register floors at 1 A, so a target below that cannot be
represented as a limit. The allocator carries each controller's `min_current_a`
and, when a target falls below it (or to zero), emits `disable = true` with a 0 A
target rather than an unachievable limit. The actuator layer maps `disable` to
the EPEver charge coil off; the Classic accepts a true 0 A current limit on its
volatile write path. Keeping the floor in the input (not the allocator) keeps the
allocator device-agnostic.

## Telemetry

Every actionable decision should be logged as a structured event:

```json
{
  "bms_ccl_a": 40.0,
  "estimated_load_a": 18.0,
  "reserve_a": 5.0,
  "budget_a": 53.0,
  "battery_charge_a": 38.0,
  "classic_target_a": 28.0,
  "epever_target_a": 25.0,
  "classic_actual_a": 30.0,
  "epever_actual_a": 24.0,
  "weight_basis": "pv_power",
  "reason": "normal_load_allowance"
}
```

Those records are how we tune reserve, deadband, rate limits, and apportionment
weights from real sunny-day traces.

## Operator controls

### Toggling (version-controlled, by design)

On/off authority lives in a flag in the supervisor systemd unit
(`config/systemd/offgrid-supervisor.service`), **not** an env var — after the
legacy taper was found running live via a stray `CHARGER_CURRENT_TAPER=true`,
control authority stays auditable in git.

| ExecStart flag | Behaviour |
|---|---|
| `--charge-allocation` | **Live** — evaluates and writes per-controller limits |
| `--charge-allocation-dry-run` | Evaluates and logs decisions; writes nothing |
| (neither) | Off |

- **Toggle:** edit the flag in the unit and run `scripts/deploy.sh` (renders the
  unit and restarts). Use the dry-run flag to watch decisions without acting.
- **Mutual exclusion:** the supervisor refuses to start `--charge-allocation`
  while the live taper is enabled (`CHARGER_CURRENT_TAPER`). The allocator is the
  sole current-limit writer; disable the taper first.
- **Emergency off without a deploy:** `systemctl stop offgrid-supervisor` (stops
  all supervision) or drive the controllers via `POST /api/v1/control/...`. The
  BMS hard limits remain the backstop regardless.

### Tuning parameters (env, no deploy)

Tuning knobs are env vars in `/etc/offgrid-power.env` — edit, then
`systemctl restart offgrid-supervisor` (no deploy). Unset → the defaults below.
This is the opposite split from the toggle on purpose: tuning values are not
safety authority, and the Phase-2 ceiling re-tune wants fast iteration. Each var
is the config field name upper-cased with its prefix; a non-numeric value is
ignored (logged) and falls back to the default.

Allocator (`CHARGE_ALLOC_…`):

| env var | default | meaning |
|---|---|---|
| `CHARGE_ALLOC_RESERVE_A` | 5.0 | margin subtracted from the budget |
| `CHARGE_ALLOC_FEEDBACK_TOLERANCE_A` | 1.0 | clamp deadband above CCL |
| `CHARGE_ALLOC_MIN_WRITE_DELTA_A` | 2.0 | skip a write within this of the present limit |
| `CHARGE_ALLOC_MIN_ACTIVE_WEIGHT_A` | 1.0 | sunrise weight floor (output basis) |
| `CHARGE_ALLOC_MIN_ACTIVE_WEIGHT_W` | 10.0 | sunrise weight floor (PV-power basis) |
| `CHARGE_ALLOC_CLASSIC_MAX_A` | 100 | Classic operator ceiling |
| `CHARGE_ALLOC_EPEVER_MAX_A` | 100 | EPEver ceiling (its rated value is preferred when read) |
| `CHARGE_ALLOC_HEARTBEAT_S` | 300 | seconds between trace events when nothing changes |

Battery-state ceiling (`CHARGE_CEILING_…`) — the Phase-2 re-tune targets:

| env var | default | meaning |
|---|---|---|
| `CHARGE_CEILING_BULK_SOC_PERCENT` | 85 | below → no SOC ceiling |
| `CHARGE_CEILING_RAMP2_SOC_PERCENT` | 92 | bulk→ramp2 SOC knee |
| `CHARGE_CEILING_FULL_SOC_PERCENT` | 100 | full-charge latch trips at/above |
| `CHARGE_CEILING_FULL_RESET_SOC_PERCENT` | 98 | latch clears below (with voltage) |
| `CHARGE_CEILING_BULK_VOLTAGE_V` | 53.6 | below → no voltage ceiling |
| `CHARGE_CEILING_RAMP2_VOLTAGE_V` | 54.4 | bulk→ramp2 voltage knee |
| `CHARGE_CEILING_TOP_VOLTAGE_V` | 54.8 | at/above → `ramp2_low` |
| `CHARGE_CEILING_FULL_RESET_VOLTAGE_V` | 54.0 | latch-clear voltage |
| `CHARGE_CEILING_RAMP1_HIGH_CURRENT_A` | 30 | ceiling at the bulk knee |
| `CHARGE_CEILING_RAMP1_LOW_CURRENT_A` | 20 | ceiling at the ramp2 knee |
| `CHARGE_CEILING_RAMP2_HIGH_CURRENT_A` | 10 | ceiling entering the top |
| `CHARGE_CEILING_RAMP2_LOW_CURRENT_A` | 4 | ceiling at full / top voltage |
| `CHARGE_CEILING_HIGH_CELL_STOP_V` | 3.55 | hard cell-voltage stop |
| `CHARGE_CEILING_HIGH_CELL_SOFT_LIMIT_V` | 3.50 | soft cell limit (with delta) |
| `CHARGE_CEILING_HIGH_DELTA_STOP_MV` | 175 | cell-delta stop |

### Reading what it's doing

- **Live:** the "Charge Allocation" panel on the console (Mode, Limits CCL/ceiling,
  Budget, per-controller targets; `*` marks a write this cycle) and the
  `allocation` block in `GET /api/v1/snapshot`.
- **History:** `charge_allocator` / `allocation_decision` events in the metric
  store (and the B2 Parquet export); the `reason` field is the quickest read of
  why a cycle did what it did.

## Implementation status

Done: pure allocator + tests; dry-run telemetry; live writes behind the flag
(Classic volatile limit, EPEver register + coil) with taper mutual exclusion;
battery-state ceiling (top-knee taper + cell safety + full-charge latch) combined
via `min()`; the unconstrained short-circuit; env-tunable parameters.

Remaining: re-tune the ceiling thresholds for the 55–56 V operating point from a
full charge cycle of traces (Phase 2); finish removing the legacy taper (Phase 4
below).

## Sunsetting the legacy taper (`charger_taper`)

The allocator is meant to replace `charger_taper`, which today writes a single
controller's current limit. They **cannot both run live** — two independent
writers fight over the same `0x9013` / Classic limit register. (We watched the
env-enabled taper, `CHARGER_CURRENT_TAPER=true` + `TARGET=epever`, fight an API
write down to its taper value in real time.)

The taper's behaviors — including the **safety** ones — are now ported into the
allocator's battery-state ceiling (`charge_ceiling.py`): the **top-knee taper**,
the **high-cell-voltage stop**, and the **full-charge latch**. Native controller
CV regulation and the BMS's own protection remain in place underneath. So the
last lines of defense are intact, and the allocator now owns the system-level
clamp — the remaining work is calibration and removing the dead machinery.

Phases:

0. **Disable the live taper, hand the knob back.** ✅ Done 2026-06-16:
   `CHARGER_CURRENT_TAPER=false` in the Pi env, EPEver limit restored to 80 A via
   the control API. Taper writes ceased.
1. **Port the per-controller ceiling into the allocator.** ✅ Done —
   `charge_ceiling.py` (top-knee taper + high-cell stop + cell-delta stop +
   full-charge latch), combined with the BMS CCL by `min()`. Thresholds are
   inherited from the taper and still need re-tuning (Phase 2).
### Ceiling signal redesign — taper on cells, not SOC (design direction 2026-06-17)

The ceiling today tapers on an SOC ramp + a pack-voltage ramp. Reconsider: the
risk is *cell overvoltage*, so taper on the signals that measure it directly.

- **Drop the SOC ramp.** SOC is a coulomb-counted/voltage-corrected estimate and
  this pack has never been cycled to the rails, so its capacity self-assessment
  is a guess — the weakest input for deciding when to back off.
- **Demote pack voltage too** — it's balance-blind (`N × avg cell`), so it
  under-reports the lead cell under imbalance. The Cubix gives max cell directly.
- **Taper on max cell voltage, pulled earlier by max–min delta.** This is the
  actual risk, measured, and is a sharp/live signal in the knee (where we taper)
  even though LFP voltage is flat mid-range (where we don't). Wide delta = lead
  cell running away → back off sooner and give the balancer time.
- **Replace the SOC≥100 full-charge latch with CV-termination**: max cell at the
  CV target *and* charge current tapered below a small fraction — more robust and
  the textbook "full" definition.
- **Live calibration anchor (2026-06-17 16:31):** ceiling pinned at 9.2 A while
  BMS CCL = 200 A, SOC 93%, pack 54.62 V, **max cell 3.425 V, delta 21 mV**. The
  SOC (>92) and pack-voltage (>54.4) ramps were throttling hard while the cells
  were barely into the knee. Operator reference points: max cell 3.425 V = full
  current, no concern; **delta alarms only above ~100 mV** (one cell running
  away). So the cell-voltage taper should hold full current to ~3.45 V/cell,
  taper 3.45→3.50 (soft), stop ~3.55; delta benign <~50 mV, ease ~70–100 mV,
  operator-alarm 100 mV, hard stop ~150–175 mV. At low delta (well balanced)
  pack voltage ≈ 16× max cell, so an interim env re-tune can recalibrate the
  voltage ramp to cell-equivalents (≈55.2/55.6/56.0 V for 3.45/3.475/3.50) and
  push the SOC knees to ~96/99 to neutralize the uncalibrated-SOC contribution.
- Tune thresholds to *under-charge* (IR-inflated) cell voltage, not rested OCV;
  keep a conservative fallback when per-cell readings are missing; smooth the
  cell-voltage input (twitchy under fluctuating current). The BMS already walks
  CCL down off its own cell monitoring, so this layers a finer/earlier
  cell-voltage taper on top, with SOC out of the loop.

**Validation & how to observe (decided 2026-06-17 — leave conservative, watch first).**
Rather than loosen the ceiling pre-emptively, observe the live knee dynamics and
let them drive the re-tune. Use the **BMS-CCL-vs-ceiling gap** as the headline
diagnostic — it directly measures how much the ceiling is leading (over-tapering
relative to) the BMS's own cell-aware walk-down:

- **Both delta climbing toward ~50 mV *and* the BMS CCL walking down off 200 A**
  → the ceiling is landing near the genuine knee; it's "not insanely wasteful,"
  just possibly engaging too early / too steeply and misbalancing across arrays
  (the issue is *degree*, not *kind*). Observed 2026-06-17: the BMS walked to
  **40 A** while the ceiling held ~9 A — a ~4× spread, consistent with
  "too early/too steep," and confirming the bank really is in the knee (not a
  phantom SOC/pack-voltage trip).
- **Delta staying flat in the 20s mV *and* the BMS CCL never leaving 200 A** →
  the ceiling is tapering on phantom (SOC/pack-voltage) risk and leaving harvest
  on the table — the clear case for the cell-voltage re-tune.

Caveat when interpreting the observation: at the throttled current the ceiling
imposes (~9 A), there is little IR push, so the cells can creep up slowly and
**plateau below where the BMS would clamp** — i.e., the conservative ceiling can
partly *suppress* the very dynamics being observed. (Less of a concern once the
BMS has already walked down, as on 2026-06-17, which shows the bank reached the
knee anyway.) So the two refined targets stand: the cell-voltage re-tune closes
the ceiling-vs-BMS gap (engages where the BMS does, not 4× sooner), and the
priority allocation ensures whatever current the ceiling *does* allow flows to
the shade-advantaged array first rather than flooring it.

### North star — a self-calibrating taper (aspirational, 2026-06-17)

Hand-set thresholds go stale: as cells age and balance/capacity/season drift,
today's good constants drift out of tune. The long-term aim is a taper that
**tunes its own thresholds from observed behavior** (cell voltages, SOC, BMS
clamping) across many days, optimizing to *reliably reach and hold full charge*
(CV-termination) under diverse irradiance, **without ever stressing cells**
(hard constraint, never a soft trade for harvest). Most of this needs no ML:

- **The BMS is already the calibrated teacher.** Its CCL walk-down is a
  cell-aware self-calibrating taper — the ground truth to approximate. The
  highest-leverage adaptive move is to *shadow* it with a small smooth lead, not
  learn a taper from scratch.
- **Fast inner loop — track, don't lead.** Online-estimate the cell voltage at
  which the BMS begins walking CCL down; set the taper onset just below it; drive
  the ceiling-vs-BMS gap to a small target. ~1 learned parameter, interpretable,
  safe — captures most of the win.
- **Slow outer loop — daily completion ratchet.** Per day, record whether
  CV-termination was reached and whether current was *ceiling*-limited while
  cells were below the knee. Non-completing + ceiling-limited → loosen onset a
  notch; any cell neared the stop → tighten. Bounded steps, hard cell-safety
  clamp. Learns over days with a simple integral controller, no ML.
- **Data-driven delta response.** Learn the delta beyond which a cell actually
  runs toward the stop; set the ease threshold from that (the 50/100 mV operator
  intuition becomes a measured number).
- **Full optimization (far end).** Treat the threshold vector as params, daily
  reward = reached-full + harvest − cell-stress penalty, optimize over many days.
  Hard parts to respect: **non-stationarity** (aging/season → continual, not
  one-shot); **safe exploration** (can't try aggressive settings on a real pack →
  ratchet only in the safe direction, or learn a cell model and explore offline
  in sim); **confounded sparse signal** (one day = one weather-confounded sample
  → slow convergence, hard credit assignment).
- **Substrate already exists.** The SQLite telemetry already logs per-cycle cell
  voltages, SOC, BMS CCL, and allocator decisions — that is the training corpus.
  Pragmatic order: ship the cell-voltage taper → add the track-the-BMS inner loop
  → run the daily ratchet → keep logging until the corpus supports an offline
  cell model to optimize against.

### The voltage race — why the EPEver rests while the Classic works (2026-06-17)

The allocator controls the **current** layer (per-charger limits). Whether a
controller works or *rests* is decided one layer up, in the **voltage** layer,
which the allocator does not touch — which is why current-priority alone can't
fix it.

> **Live experiment in progress (2026-06-17 17:16):** EPEver setpoints bumped to
> **Classic + 0.2 V** (boost/equalize 56.4, float 54.9) via
> `sync-from-classic` to test whether a setpoint *lead* wakes it. Couldn't be
> evaluated same-day — applied too late, the shaded array was already at 0 W
> (bus 55.73 < boost 56.4, coil on, allocator allowing 1 A, but no PV to harvest;
> bank effectively full at max cell 3.501 V / 94% SOC). Setpoints persist and are
> safe to leave (3.525 V/cell < 3.55 stop, BMS guards). **To validate:** next
> productive window with bus < 56.4 — does the EPEver carry current while the
> *Classic* eases to rest? Revert with `charge-sync-epever.sh 0`.

- The EPEver has **no battery comms** — it knows only the voltage at its own
  terminals, and its charge stages (Bulk/Boost/Float/**Resting**) are driven
  purely by that voltage vs its internal setpoints. "Battery looks full" *means*
  "terminal voltage reached my boost/float target" — it has no SOC.
- Two voltage-source chargers on one bus is a **race the higher setpoint wins**:
  whichever controller has the higher absorb/boost target pushes the bus up to
  *its* target; the lower-target controller sees its own target already met and
  drops to float/rest. So if the Classic's absorb ≥ the EPEver's boost, the
  Classic holds the bus and the **EPEver rests** — not because it's full or
  faulty, but because its setpoint isn't the one in charge of the bus. (This is
  why *tapering the Classic made the EPEver take over* — proof of the mechanism.)
- "**Eagerly**": the EPEver senses at its own terminals, so the Classic's current
  adds an IR rise across the shared wiring → the EPEver reads *higher* than true
  battery voltage and rests even sooner than setpoints alone predict.
- The allocator then **compounds** it: a resting/idle EPEver gets floored to ~1 A,
  so even setting the voltage layer aside it's capped out of contributing.
- **Design implication for the EPEver-priority work:** current-limit priority
  *alone won't* make the shade-advantaged array do the work — its voltage setpoint
  keeps sending it to rest regardless of current headroom. Privileging it needs
  **both** levers: raise the EPEver boost/absorb setpoint *above* the Classic's
  (`charge-sync-epever.sh`, positive offset) so it leads the bus, **and** give it
  allocator current-priority. The voltage offset is the more fundamental of the two.

### Field findings (2026-06-17)

- **Disable-on-lost-split bug (fixed).** The allocator was switching a charger's
  coil OFF when it lost the apportionment split: a producing, eligible EPEver
  whose PV-power weight dipped below the Classic won a sub-1 A share, which
  mapped to `disable`. With the Classic dominating morning production this
  flapped the EPEver coil on/off and showed it "Resting" at 79–80% SOC while it
  had PV. Fix: `disable` now only fires for genuine stops (charge-disabled,
  CCL/ceiling ≤ 0, latch, cell safety); a sub-`min_current` share in
  apportionment floors at `min_current` instead. Strictly fewer coil-offs.
- **Apportionment cycling (Phase 2, in progress).** Per-cycle PV-power weights
  swing with cloud/MPPT noise, so the split — and thus the written limits —
  cycled wildly and looked unbalanced near full. Added an **EMA on each
  charger's PV power** in the allocation logger (`CHARGE_ALLOC_PV_SMOOTH_ALPHA`,
  default 0.25) so momentary swings don't whipsaw the split. The total budget is
  unchanged, so this is split-stability only.
- **Still conservative near full / over-rationing.** The remaining structural
  issue: the "unconstrained" short-circuit keys on combined *nameplate* max
  (≈ CCL) rather than actual headroom, so it rations whenever the maxes sum near
  CCL even though real output is a fraction of it. **Observed 2026-06-17:** under
  a ~1500 W house load the budget correctly grew to include it (~37 A), but the
  PV-power split then capped Classic 29 A / EPEver 7.6 A — non-transferable caps,
  so a PV-limited Classic's unused headroom couldn't flow to the EPEver and the
  load went partly unfed (battery discharged). Fix: when *measured* total charger
  output is at/below the budget, don't apportion — pin both to max so neither
  leaves the load underfed; only split once production genuinely exceeds the
  budget.
- **Constrained-case policy: per-controller priority, not proportional split**
  (operator design input 2026-06-17). When the budget *is* genuinely scarce
  (deep in the knee), don't split proportionally by PV power — allocate in a
  **priority order**: fill the highest-priority controller to its available
  output first, give the next its share of what remains, and only trim the
  *lowest*-priority controller. **Array 1 (EPEver) is the high-priority array:**
  it was added specifically to lift *cloudy-day* production, and a lightly-shaded
  array is barely penalized under diffuse light, so it has a comparative
  advantage in poor conditions. So the EPEver should run with few constraints
  until we are deep into the knee; the Classic (array 0) carries the constraint
  first. This also fixes the "EPEver stuck at 1 A" behavior — under priority it
  runs free (its small output is innocuous to the budget) instead of getting the
  short end of a proportional split. Implies a per-controller priority input
  (operator-configurable, EPEver > Classic by default). The ceiling SOC ramp may also
  be tunable upward at high SOC (the bank took up to ~13 A at 90–95% in the
  traces, vs the ~9 A ceiling). Next: drive "unconstrained" off measured
  output/headroom, and re-tune the SOC ramp from data.

2. **Validate / re-tune.** ⏳ In progress. First re-tune applied 2026-06-17 from
   one charge cycle of live traces (journal failure analysis + stored SOC/V/I):
   the bank charges **53–56.5 V (median 55.1)**, so the old voltage thresholds
   (53.6/54.4/54.8) sat *below* the operating point and pinned the ceiling to
   4 A through most of absorption — **18% of charging samples were over-clamped**,
   throttling a bank taking up to ~13 A at 88–95% SOC down to ~4 A. The SOC ramp
   was fine; only the voltage ramp was wrong. Shifted via env (no deploy) to
   bracket the real absorb range:
   `CHARGE_CEILING_BULK_VOLTAGE_V=55.0`, `RAMP2_VOLTAGE_V=55.8`,
   `TOP_VOLTAGE_V=56.4`, `FULL_RESET_VOLTAGE_V=54.5`. These live in
   `/etc/offgrid-power.env` (not git); **promote to the `ChargeCeilingConfig`
   code defaults once validated over more cycles.** Still to watch: a full
   charge to the 55–56 V absorb plateau under the new thresholds (the re-tune
   day started at 77% SOC / 53 V, below the knee).
3. **Make the allocator the sole live current writer.** ✅ Done — `--charge-allocation`
   live path writes the Classic limit and the EPEver register/coil; the supervisor
   refuses to start with both it and the live taper (mutual exclusion).
4. **Remove the machinery:** `charger_taper.py`, `apply_charger_current_taper`,
   the `--charger-current-taper*` / `--classic-current-taper*` flags and the
   `CHARGER_CURRENT_TAPER*` env handling (and the Pi env lines + `.env.example`),
   the `ChargerTelemetry` / `ChargerCurrentSettings` helpers, and
   `tests/test_charger_taper.py`. Leave historical `charger_taper` events in the
   store untouched; note the producer's retirement in the journal.
