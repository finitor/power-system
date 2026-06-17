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
  CCL even though real output is a fraction of it. The ceiling SOC ramp may also
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
