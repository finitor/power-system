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
controller overshoot. Downward changes should be immediate; upward changes
should be rate-limited in the live writer.

The clamp is a unity-gain proportional correction, and `reserve_a` is its
companion margin. If the load estimate is biased high by more than the reserve,
the feed-forward over-allocates and the clamp pulls net battery current back down
to exactly CCL in steady state — safe, just at the ceiling. So `reserve_a`
absorbs load-estimate error in the safe direction and the clamp catches the
unsafe direction. The residual risk is transient overshoot and oscillation given
the chargers' heterogeneous response (the EPEver's soft-start lag vs the
Classic), which is why the allocator stays stateless and rate-limiting lives in
the live writer — immediate down, slow up.

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

## Implementation Plan

1. Add a pure allocator module with deterministic tests for budget calculation,
   feedback clamping, and two-controller apportionment.
2. Add dry-run telemetry events from the supervisor loop.
3. Add live writes behind an explicit flag:
   - Classic: volatile charge-current limit.
   - EPEver: max charging current register, with coil-off below useful minimum.
4. Add rate limiting and stale-data guards before enabling live writes by
   default.
