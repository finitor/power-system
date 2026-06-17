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

## Apportionment

Eligible controllers receive a share of the budget. A good first strategy is
proportional-by-recent-output:

- Online and enabled controllers are eligible.
- A controller in an active charging stage gets weight from recent actual
  output current, with a small minimum weight so it is not starved at sunrise.
- Unavailable controllers get no target.
- Each target is capped by that controller's configured/operator maximum.
- Unused budget from a capped controller is redistributed to the remaining
  eligible controllers.

This naturally gives the larger or sunnier array more budget without hardcoding
array sizes, while still letting the other controller participate.

## Safety States

- **Disabled:** BMS charge-enable false or CCL <= 0. Command active chargers to
  zero/off.
- **Conservative:** BMS CCL or pack current stale/missing. Use a small fallback
  or no charge.
- **Normal:** Allocate `CCL + load - reserve`.
- **Clamp:** If measured net battery current exceeds CCL, subtract the excess
  immediately.
- **Recovery:** Restore headroom slowly after the clamp clears.

EPEver's current register floors at 1 A, so a target below that should map to
the charge-enable coil off in the live actuator layer. Classic can accept a true
0 A current limit on the volatile write path.

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
