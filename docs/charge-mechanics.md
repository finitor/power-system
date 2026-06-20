# Charge Mechanics and Tuning to Full

How charge current actually flows into the LiFePO4 bank, and how to choose
voltage setpoints and current limits that bring it to full capacity without
overstressing cells. This is the *why* underneath the two operational docs:

- [Charge Management](charge-management.md) — the policy/guard layer (BMS
  CVL/CCL signals, Classic write modes, cell-voltage guardrails).
- [Real-Time Charge Current Allocation](charge-current-allocation.md) — the
  closed-loop allocator that distributes a current budget across controllers.

If you only remember one thing: **current limits are ceilings, voltage is the
driver.** A high current limit never pushes a single amp; it only removes a cap.
What pushes charge is the difference between the bus voltage and the battery's
own internal voltage.

## The electrical model

Model the bank as an EMF source — its open-circuit voltage `V_oc`, a function of
state of charge — in series with a small internal resistance `R_int` (cell
electrochemistry + busbars + cabling + shunt). Everything hangs off one DC bus:
the charge controllers (voltage sources, when they have the power to be), the
inverter/house load, and the battery.

The charge current into the battery is just Ohm's law around that loop:

```
I_batt = (V_bus − V_oc) / R_int
```

- `V_bus` — the common bus voltage. A controller in voltage-regulation mode
  tries to hold this at its setpoint (absorb/float); in current-limited mode it
  pushes all the current it has and the bus floats wherever the loads/battery
  settle it.
- `V_oc` — the battery's open-circuit voltage, set by SOC. Rises as the pack
  fills.
- `R_int` — total series resistance, typically tens of milliohms for this bank
  including wiring and the shunt.

Two consequences fall straight out of this equation and explain almost
everything operators see:

1. **While charging, the terminal voltage reads high.** `V_term = V_oc + I·R_int`.
   The pack you measure at 10 A is above its true OCV by `I·R_int`. When current
   stops, that `I·R_int` term vanishes and the terminal relaxes back to `V_oc`.
   So a "resting" voltage reading is `V_oc`; a "charging" reading is inflated.
2. **A current ceiling can't create current.** BMS CCL, the controller's max-
   charge-current register, and the allocator's allowance are all upper bounds.
   If `(V_bus − V_oc)` is ~0, then `I_batt` is ~0 no matter how high those
   ceilings sit.

## The LiFePO4 curve and the knee

LiFePO4 open-circuit voltage is famously flat across the middle of the SOC range
(~20–90%) and rises sharply at the top. Said in calculus: `dQ/dV` (the charge
stored per volt) is enormous in the flat region and tiny near full. For a 16S
bank (~3.2 V/cell nominal, ~51.2 V):

| Zone | Per-cell `V_oc` | Pack `V_oc` | Behavior |
|---|---|---|---|
| Bulk / flat | ~3.30–3.45 V | ~52.8–55.2 V | A small ΔV sustains large current a long time |
| Knee | ~3.45–3.50 V | ~55.2–56.0 V | Curve steepens; current starts to taper |
| Top | ~3.50–3.55+ V | ~56.0–56.8 V | A few coulombs move the voltage a lot; current self-extinguishes |

This shape is the single most important fact for tuning. Down in the flat zone a
charger holding the bus a little above OCV will push current for a long time,
because adding charge barely moves `V_oc`. Up at the knee the same ΔV
self-extinguishes almost immediately, because each coulomb you add jumps `V_oc`
right up to meet the bus — closing the gap that was driving the current.

The same steepness is why cell-to-cell imbalance becomes visible (and matters)
only near the top, and why the guardrails watch cell delta as a function of
max-cell voltage rather than absolutely — see
[Threshold Theory](charge-management.md#threshold-theory).

## Charging stages mapped to the model

The classic bulk / absorb / float stages are just two operating regimes of the
same Ohm's-law loop:

- **Bulk = constant current (CC).** The pack is well below the setpoint, so the
  controller would have to push huge current to reach it; instead it runs at its
  current ceiling (PV-limited, its own max-charge register, or the allocator's
  limit). The bus rides at `V_oc + I·R_int`, climbing as the pack fills. **The
  binding constraint here is current.**
- **Absorb = constant voltage (CV).** The pack voltage has risen to the setpoint;
  the controller now holds the bus at the absorb voltage and *current tapers on
  its own*: `I = (V_set − V_oc)/R_int`, and as charge flows `V_oc → V_set`, so
  `I → 0`. **The binding constraint here is voltage.**
- **Float = CV at a lower maintenance voltage.** Holds the pack without forcing
  charge; sources mainly the house load once the pack is satisfied.

The transition from bulk to absorb — where the limiter changes from current to
voltage — happens at the knee. **Everything above the knee is voltage-limited.**
That is the regime where "more current limit" stops doing anything and only the
voltage setpoint (and how long you hold it) decides how full you get.

## Worked example: idle at 97% in full sun

Observed one bright afternoon (arrays capable of ~1000 W):

- SOC 97%, battery resting ~56.46 V (≈3.53 V/cell), flow ~0.0 A — idle.
- Both controllers in Float, output ~56.5 V, producing only enough to cover the
  ~3.8 A house load.
- Classic absorb/float setpoints ~0.2 V above the resting pack voltage.
- BMS CCL advertised 40 A; controller current limits ~15 A — both far above the
  near-zero current actually flowing.

Why was the battery taking nothing? Work it by elimination:

- Power was available (full sun) → **not** supply/PV-limited.
- CCL was 40 A → BMS permission wasn't the cap.
- Controllers were under their ~15 A limits → current limit wasn't the cap.
- Charge-enable was true, no protection/alarm → the BMS wasn't gating the path.

With power, permission, and current headroom all present and current still ~0,
the only term left is **voltage headroom**. The bus was resting at the pack's OCV
(~56.46 V), not being held up at the 56.66 V setpoint — in float the controllers
regulate within a band, and a rested pack already sitting inside that band gets
no push. And even if a controller *had* lifted the bus 0.2 V, the steep `dQ/dV`
at 97% means a few coulombs would raise `V_oc` to meet it and choke the current
back off within seconds. This is simply the CV taper run to completion: the pack
is, for practical purposes, full at that setpoint.

The capacitor analogy is exact: a nearly-charged capacitor in series with `R`.
`I = (V_set − V_cap)/R`, and as it charges `V_cap → V_set` so `I → 0`. In the
flat SOC zone the "capacitance" (`dQ/dV`) is huge and the current persists; at
the knee it's tiny and the current self-extinguishes. The battery was not
refusing charge — the chargers, at that setpoint, had nothing left to offer.

**Distinguishing the look-alikes.** Same idle symptom, different cause:

| Symptom | Cause | Tell |
|---|---|---|
| Idle, controllers **under** limit, sun available | Voltage-limited (CV taper done) | bus ≈ resting `V_oc`; setpoint only just above |
| Idle, controllers **maxed**, low sun | Power/PV-limited | controller output ≈ load; PV current tiny |
| Idle, charge-enable false or CCL 0 | BMS gating (protection/full latch) | snapshot shows charge disabled / CCL 0 |
| Idle, bus held **above** `V_oc` yet ~0 A | BMS opened the charge MOSFET | output clearly > rested pack V, still 0 A |

## What each limit actually controls

- **Current ceilings** — controller max-charge register, BMS CCL, allocator
  allowance/per-controller limits. These cap the *bulk* stage and share the
  budget across controllers; they protect against overcurrent. Above the knee
  they are slack and irrelevant — current is already self-limiting.
- **Voltage setpoints** — absorb, float, equalize, max temp-comp. These are the
  *driver*: they decide how full the pack gets and how hard the knee is pushed.
  They are guarded against BMS-published CVL ([Charge Management](charge-management.md)).
- **Absorb time** — how long the controller holds the absorb voltage after the
  pack reaches it. This is what finishes the last few percent: at constant
  voltage the current tapers, and the *integral* of that tapering current over
  the hold time is the final charge that tops the pack off and lets passive
  balancing pull laggard cells up.

So "reach full capacity" is almost entirely a voltage-and-time question, not a
current question.

## Tuning to full

The goal is to push `V_oc` up to a chosen full point and let the cells settle
there, without sitting so high that cell delta diverges or longevity suffers.

1. **Pick an absorb voltage.** Higher absorb → fuller pack but more time spent on
   the stressful steep part of the curve and more chance of cell-delta
   divergence. A practical full-but-gentle target for this 16S bank is in the
   ~3.45–3.50 V/cell range (~55.2–56.0 V); going to ~3.55 V/cell (~56.8 V) gets
   the last sliver of capacity at rising risk. Always under BMS CVL — the guard
   will refuse anything above it.
2. **Give it absorb time.** A setpoint the pack only touches for a moment barely
   adds charge (the taper hasn't run). Holding absorb long enough for the current
   to taper toward a small fraction of capacity (a common target is ~C/50–C/100)
   is what actually fills it and lets balancing work.
3. **Size current limits for bulk only.** Set them so bulk delivers what the
   array and BMS CCL allow; they do nothing near the top, where the taper is
   self-limiting. The allocator's job is sharing this budget and keeping net
   battery current within the BMS allowance, not forcing the last few percent.
4. **Use the CCL scaling factor to shape knee aggressiveness.** Below the BMS
   knee baseline the allocator takes a fraction of CCL as the working budget;
   raising it pushes harder near the top, lowering it backs off. See
   [Nudging the CCL scaling factor](charge-current-allocation.md#nudging-the-ccl-scaling-factor-live-no-restart).
5. **Explore the knee with the voltage nudge.** Because the taper near the top is
   so setpoint-sensitive, the cleanest way to find the right absorb is to nudge
   it ±0.1 V and watch the response: a small bump reopens `(V_set − V_oc)` and
   the controllers source a brief charge pulse that tapers as `V_oc` catches up.
   Watch cell delta as you do — if max cell climbs into the upper zone and delta
   rises, you're past the gentle point. Tools: tune mode (`t`) on the console, or
   `scripts/charge-controller-voltage.py --by ±0.1`
   ([display-services.md](subsystems/display-services.md#operator-controls-terminal-display)).

### Reading the signs

- **Idle at the setpoint, cells balanced** → the CV taper is complete; the pack
  is full *at this setpoint*. To go fuller you must raise the setpoint, not the
  current limit.
- **Cell delta rising as max-cell enters the upper zone** → balancing is lagging
  the charge; hold (or back off) rather than pushing higher. See
  [Threshold Theory](charge-management.md#threshold-theory).
- **BMS CCL tapering down on its own near full** → the battery is requesting a
  slower top-off; the allocator already treats CCL as an input, so let it.
- **Terminal voltage well above resting OCV under charge** → normal `I·R_int`;
  don't confuse it with a high setpoint. Judge fullness by the *rested* voltage.

## Field notes

- 2026-06-20 — Documented the 97%-idle-in-full-sun observation above and the
  elimination argument that isolates it as the voltage-limited CV-taper endpoint
  rather than a BMS refusal. Motivated writing this doc as the conceptual basis
  for the operator voltage/CCL-scaling nudge tools.
