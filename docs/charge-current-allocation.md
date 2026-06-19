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
for each controller from one coherent snapshot, resolves the charge allowance,
calls the pure allocator `decide()`, logs the decision (on material
change + a heartbeat), and — when live — writes the per-controller limits.
`ChargeCeiling` resolves the net charge-current allowance first; `decide()` then
only distributes that allowance across controllers. The one piece of state (the
full-charge latch) lives in `ChargeCeiling`. The cycle resolves in this order:

1. **Missing BMS data → no action.** If BMS CCL or pack current is unreadable,
   return no targets and write nothing (fail safe; never guess). Missing
   charge-controller telemetry only removes that controller from the eligible
   set.
2. **Resolved allowance.** `ChargeCeiling.evaluate()` returns one of three
   states:
   - `None`: unconstrained, release controller limits to max.
   - positive amps: constrain net battery charge to that allowance.
   - `0 A`: hard stop; command every charger off.
3. **Unconstrained short-circuit.** If every eligible charger at its own max
   still couldn't reach a positive allowance — `Σ(max) ≤ allowance` — the
   allocator is *not* the binding constraint (sunlight is). Pin each charger to
   its own max: no reserve, no apportionment, no per-cycle writes. This is the
   normal state for most of a sunny day.
4. **Budget.** Otherwise `budget = allowance + max(load, 0) − reserve`,
   then the feedback clamp (see Budget).
5. **Apportion** the budget evenly across eligible controllers (water-fill with
   cap redistribution), quantized to whole amps.
6. **Targets → writes.** Each target is capped at the controller's max and
   represented as a whole-amp limit. The logger then makes non-emergency targets
   sticky: changes inside `target_deadband_a` are held at the current controller
   setting, and larger changes snap to `target_quantum_a` buckets. Live writes
   are still gated by `min_write_delta_a`: the Classic limit is written volatile,
   the EPEver current register is written, and the EPEver charge coil is toggled
   only for battery/control-side disable intent.

The binding limit — `unconstrained`, `BMS CCL fraction`, `feedback_clamp`,
`full-charge latch`, a cell stop, or a
disable reason — is recorded as the decision `reason`, so a trace shows *why*
each cycle did what it did.

### Charge Budget Resolver

`ChargeCeiling.evaluate()` is the single policy function for charge budget and
hard stops:

- **BMS baseline → unconstrained:** if BMS CCL is still at/above
  `bms_knee_ccl_baseline_a` (default 200 A), return `None`.
- **BMS knee → fractional allowance:** once BMS CCL drops below baseline, return
  `BMS CCL × bms_ccl_budget_fraction` (default 50%).
- **Cell-safety stops → 0 A:** max cell ≥ `high_cell_stop_v`, or cell delta ≥
  `high_delta_stop_mv` while max cell ≥ `high_cell_soft_limit_v`. Cell-safety
  stops latch until max cell falls below `high_cell_soft_limit_v`, preventing
  coil chatter around the hard threshold.
- **Low-temperature stop → 0 A:** minimum reported cell temperature ≤
  `low_temp_stop_c` (default 0 C). If minimum cell temperature is unavailable,
  use the pack temperature from the ordinary BMS measurements frame. The stop
  latches until that temperature reaches `low_temp_recover_c` (default 2 C).
- **Full-charge latch → 0 A:** once SOC reaches `full_soc_percent`, hold zero
  until the pack rests (SOC < `full_reset_soc_percent` **and** voltage ≤
  `full_reset_voltage_v`). This is the one piece of carried state.

## Inputs

Use one coherent supervisor snapshot per decision:

- BMS CCL, charge-enable flag, pack current, cell voltage/delta, and battery
  temperature.
- Classic actual output current, present charge-current limit, stage/state.
- EPEver actual output current, present charge-current limit, stage/state.
- Estimated household load current.

The existing load estimator already uses the right bus balance, summing whatever
charge-controller telemetry is present:

```text
load current = known charge-controller output - BMS net battery current
```

If one controller is offline and apparently disconnected from the bus, the
remaining controller can still receive the full budget plus the observed load
allowance. If a silent controller is secretly still producing, its hidden output
shows up through BMS net current. While hidden production is less than household
load, that reduces the estimated load allowance for the known controller. If
hidden production pushes net battery charge above the resolved allowance, the
measured-current feedback clamp reduces the next budget. If load is unavailable,
the allocator falls back conservatively and prefers undercharging to exceeding
CCL.

### Availability and nighttime release

Availability means "this charger is a useful actuator for the allocation this
cycle", not "the PV string has open-circuit voltage." Field behavior showed both
arrays can report high PV voltage in twilight while loaded output is effectively
impossible. Therefore the allocator now uses each controller's **own charge
state** as the release trigger:

- **Classic available:** canonical stage is `Bulk`, `Absorb`, `Float`, or
  `Equalize`.
- **EPEver available:** canonical stage is `Absorb` (`Boost`), `Float`, or
  `Equalize`.
- **Resting / no charging:** first held active through a debounce window, then
  treated as inactive for allocation.

When a controller becomes inactive from its own sleep/rest state, the allocator
does **not** command it off. That is a resource-side condition (low input / the
controller's own state machine), not a battery safety event. Instead it releases
any stale constraint by targeting the controller's normal max current limit. This is
good housekeeping at sunset: late-afternoon high-SOC limits may be single-digit
amps, but once solar has waned those constraints no longer protect the battery
and should not be left in place for the next morning.

The release is debounced in the allocation logger:

- `CHARGE_ALLOC_CLASSIC_SLEEP_DEBOUNCE_S` (default 180 s)
- `CHARGE_ALLOC_EPEVER_SLEEP_DEBOUNCE_S` (default 180 s)

The EPEver coil is intentionally not part of this low-input policy. The coil is
reserved for explicit charge-stop conditions such as BMS charge disabled, CCL
zero, full-charge latch, or cell-safety stops.

## Budget

The feed-forward budget is:

```text
budget_a = max(0, allowance_a + max(load_a, 0) - reserve_a)
```

Then close the loop with BMS current:

```text
if measured_battery_charge_a > allowance_a + tolerance_a:
    budget_a -= measured_battery_charge_a - allowance_a
```

The feedback clamp catches bad load estimates, stale controller output, and
controller overshoot.

The clamp is a unity-gain proportional correction. `reserve_a` is available as
an extra margin, but defaults to `0 A` because the upstream `ChargeCeiling`
policy already uses a conservative fraction of the BMS CCL. If the load estimate
is biased high, the feed-forward over-allocates and the clamp pulls net battery
current back down to exactly CCL in steady state; the clamp's "down" is
effectively immediate because it is recomputed every cycle from measured BMS
current.

**No rate-limiting on the limit writes.** A controller's current limit is a
*ceiling*, not a setpoint: the controller still ramps its actual output via its
own CV regulation / soft-start, so jumping the limit does not surge current.
That, plus the budget-level clamp giving immediate-down behaviour, is why we do
not slow the limit writes — and not slowing them also avoids extra EEPROM writes
to the EPEver. If traces ever show oscillation, revisit this.

Targets are quantized to whole amps before logging or writing. The live logger
then adds a coarse stabilizer: by default a target must move at least 5 A from
the present controller setting before it is written, and large moves are snapped
to 5 A buckets. BMS charge-disabled, CCL-zero, full-charge latch, cell-safety
stops, and feedback-clamp decisions bypass this smoothing.

## Apportionment

Eligible controllers receive an equal share of the budget by water-filling: each
target is capped at the controller's configured/operator maximum, and budget
unused by a capped controller is redistributed to the rest. This is intentionally
simple and robust while the system is still being characterized.

- Online, enabled, and *able-to-charge* controllers are eligible (see below).
- Unavailable controllers get no target; the remaining eligible controller(s)
  receive the whole budget.

### Split signal

The allocator currently uses an equal split across eligible chargers and records
`weight_basis: "equal"` in decision traces. This replaced a PV-power-weighted
experiment: field traces showed that a charger limited to `0 A` can report near
zero PV input power and enter `Resting`, so PV weighting can latch that charger
out of the allocation until the supervisor restarts or another disturbance
reopens it. Equal split is less clever and much easier to reason about.

### Eligibility

"Able to charge" means the controller is in an active charge stage — **not**
"currently sourcing current." The supervisor must not derive eligibility from
present output, or throttling a controller toward zero would drop it from the
eligible set and it could never be handed budget back. It must also not derive
eligibility from PV voltage alone; unloaded string voltage can remain high after
the usable solar resource has faded.

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
- **Recovery/release:** Restore headroom after the clamp clears. If a controller
  has gone inactive because its own state machine is resting/no-charging, release
  its limit to max instead of constraining or disabling it.

EPEver's current register floors at 1 A, so an **eligible apportionment share**
below that cannot be represented literally. The allocator floors such a share to
`min_current_a` rather than turning the charger off. Only genuine
battery/control-side stops emit `disable = true` with a 0 A target. The actuator
layer maps that disable to the EPEver charge coil off; the Classic accepts a true
0 A current limit on its volatile write path. Keeping the floor in the input
(not the allocator) keeps the allocator device-agnostic.

## Telemetry

Every actionable decision should be logged as a structured event:

```json
{
  "bms_ccl_a": 40.0,
  "allowance_a": 20.0,
  "budget_a": 38.0,
  "load_allowance_a": 18.0,
  "battery_current_a": 16.0,
  "battery_charge_a": 16.0,
  "classic_actual_a": 30.0,
  "epever_actual_a": 24.0,
  "targets": {
    "classic": {"target_a": 19.0, "disable": false},
    "epever": {"target_a": 19.0, "disable": false}
  },
  "weight_basis": "equal",
  "reason": "BMS CCL fraction"
}
```

Those records are how we tune reserve, deadband, rate limits, and apportionment
policy from real sunny-day traces.

## Operator controls

### Toggling (version-controlled, by design)

On/off authority lives in a flag in the supervisor systemd unit
(`config/systemd/offgrid-supervisor.service`), **not** an env var — after the
an older taper loop was found running live via a stray env toggle, control
authority stays auditable in git.

| ExecStart flag | Behaviour |
|---|---|
| `--charge-allocation` | **Live** — evaluates and writes per-controller limits |
| `--charge-allocation-dry-run` | Evaluates and logs decisions; writes nothing |
| (neither) | Off |

- **Toggle:** edit the flag in the unit and run `scripts/deploy.sh` (renders the
  unit and restarts). Use the dry-run flag to watch decisions without acting.
- **Mutual exclusion:** the supervisor refuses to start `--charge-allocation`
  while the old live taper is enabled (`CHARGER_CURRENT_TAPER`). The allocator
  is the sole current-limit writer; disable the taper first.
- **Emergency off without a deploy:** `systemctl stop offgrid-supervisor` (stops
  all supervision) or drive the controllers via `POST /api/v1/control/...`. The
  BMS hard limits remain the backstop regardless.

### Tuning parameters (env, no deploy)

Tuning knobs are env vars in `/etc/offgrid-power.env` — edit, then
`systemctl restart offgrid-supervisor` (no deploy). Unset → the defaults below.
This is the opposite split from the toggle on purpose: tuning values are not
on/off authority and should be easy to iterate. Each var is the config field name
upper-cased with its prefix; a non-numeric value is ignored (logged) and falls
back to the default.

Allocator (`CHARGE_ALLOC_…`):

| env var | default | meaning |
|---|---|---|
| `CHARGE_ALLOC_RESERVE_A` | 0.0 | extra margin subtracted from the budget |
| `CHARGE_ALLOC_FEEDBACK_TOLERANCE_A` | 1.0 | clamp deadband above the resolved allowance |
| `CHARGE_ALLOC_MIN_WRITE_DELTA_A` | 1.0 | skip a write within this of the present limit |
| `CHARGE_ALLOC_TARGET_DEADBAND_A` | 5.0 | hold non-emergency targets within this distance of the current controller setting |
| `CHARGE_ALLOC_TARGET_QUANTUM_A` | 5.0 | snap larger non-emergency target moves to this amp bucket |
| `CHARGE_ALLOC_CLASSIC_MAX_A` | 80 | Classic hardware/operator ceiling |
| `CHARGE_ALLOC_EPEVER_MAX_A` | 100 | EPEver hardware/operator ceiling |
| `CHARGE_ALLOC_HEARTBEAT_S` | 300 | seconds between trace events when nothing changes |
| `CHARGE_ALLOC_CLASSIC_SLEEP_DEBOUNCE_S` | 180 | Classic Resting/no-active-stage duration before releasing stale constraints |
| `CHARGE_ALLOC_EPEVER_SLEEP_DEBOUNCE_S` | 180 | EPEver No charging duration before releasing stale constraints |

Charge budget resolver (`CHARGE_CEILING_…`):

| env var | default | meaning |
|---|---|---|
| `CHARGE_CEILING_BMS_KNEE_CCL_BASELINE_A` | 200.0 | unconstrained while BMS CCL remains at/above this |
| `CHARGE_CEILING_BMS_CCL_BUDGET_FRACTION` | 0.5 | net-charge allowance after the BMS knee gate opens, as a fraction of BMS CCL |
| `CHARGE_CEILING_FULL_SOC_PERCENT` | 100 | full-charge latch trips at/above |
| `CHARGE_CEILING_FULL_RESET_SOC_PERCENT` | 98 | latch clears below (with voltage) |
| `CHARGE_CEILING_FULL_RESET_VOLTAGE_V` | 54.0 | latch-clear voltage |
| `CHARGE_CEILING_HIGH_CELL_STOP_V` | 3.62 | hard cell-voltage stop |
| `CHARGE_CEILING_HIGH_CELL_SOFT_LIMIT_V` | 3.55 | recovery threshold and upper-cell zone for delta stop |
| `CHARGE_CEILING_HIGH_DELTA_STOP_MV` | 150 | cell-delta stop in the upper-cell zone |

### Reading what it's doing

- **Live:** the "Charge Allocation" panel on the console (Mode, Limits CCL/allowance,
  Budget, per-controller targets; `*` marks a write this cycle) and the
  `allocation` block in `GET /api/v1/snapshot`.
- **History:** `charge_allocator` / `allocation_decision` events in the metric
  store (and the B2 Parquet export); the `reason` field is the quickest read of
  why a cycle did what it did.

## Implementation Status

Live on `blueberry.local` behind `--charge-allocation`:

- Pure allocator with tests.
- `ChargeCeiling` as the single resolver for charge budget and hard stops.
- Classic volatile current-limit writes.
- EPEver max-current writes plus charge-coil reconciliation.
- Controller-state availability and nighttime release of stale constraints.
- Env-tunable budget, debounce, and guardrail parameters.
- Whole-amp targets plus coarse 5 A live stabilization for non-emergency writes.
- Snapshot/API/display fields using `allowance` for the resolved net charge
  current budget.

The old `charger_taper` module and CLI flags still exist for rollback/history,
but live allocator startup refuses to run alongside the old live taper. Current
operations should treat the allocator as the sole current-limit writer.

## Current Field Notes

### EPEver Voltage Layer

The allocator controls current limits, not charge-stage voltage decisions. The
EPEver has no battery comms; it decides Bulk/Boost/Float/Resting from terminal
voltage and its own setpoints. Field experiments on 2026-06-17 showed:

- Raising EPEver absorb/boost alone did not wake it once it had settled into
  Float/Resting.
- The useful wake lever is boost-reconnect / bulk-recovery voltage.
- Current operating experiment: EPEver leads the bus with absorb/float/EQ aligned
  at 56.4 V and recovery near the daily bus voltage, while the allocator governs
  current.

These settings live in device registers, not version-controlled config.

### 2026-06-18 Allocator Corrections

- **No disable on low input.** An EPEver left OFF after sunset traced to the
  allocator treating `charger inactive` as a hard-disable condition. Inactive or
  unavailable chargers now release to max current limit with `disable=false`.
  The EPEver coil is reserved for resolved `0 A` battery/control stops.
- **PV voltage is not availability.** Open-circuit PV voltage remains high in
  twilight. Availability now comes from controller charge state with debounce:
  Classic `Resting` and EPEver `No charging` release constraints after the
  configured sleep debounce.
- **BMS-led knee policy.** Constraints only engage after BMS CCL drops below the
  200 A baseline. The resolved allowance is currently 50% of BMS CCL; local cell
  voltage/delta checks are guardrails, not the normal knee controller.
- **Write smoothing.** Targets are whole amps; non-emergency live targets hold
  within a 5 A band and snap larger moves to 5 A buckets to reduce controller
  setting churn.
- **Cell guardrails.** Initial 3.55/3.50 V max-cell stop/recovery was too
  conservative with balanced cells around 94% SOC. Current defaults are
  max-cell stop 3.62 V, recovery/upper-zone threshold 3.55 V, and delta stop
  150 mV in that upper zone.

## Watch Items

- Afternoon/evening behavior with controller-state release: confirm both
  controllers release constraints as solar input fades and that EPEver coil does
  not remain OFF overnight.
- Near-knee behavior: confirm 50% of BMS CCL is conservative without starving
  useful charge.
- Apportionment fairness: current split is equal-share water-fill, not a
  priority allocator. Revisit if one array is consistently underutilized in
  cloudy or shaded conditions.
- Legacy cleanup: remove `charger_taper.py`, old taper flags/env handling, and
  tests once rollback value is gone.
