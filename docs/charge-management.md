# Charge Management

This system treats the battery BMS as the authority on the battery's safe operating envelope, and treats charge controllers as actuators that must stay inside that envelope. The supervisor can automatically allocate charge-controller current limits when charge allocation is enabled; otherwise it reports telemetry and write-time guard failures.

For the electrical fundamentals beneath this policy — why charge current is set by voltage difference rather than by current limits, and how to tune voltage/current to reach full capacity — see [Charge Mechanics and Tuning to Full](charge-mechanics.md).

## BMS Negotiation Signals

The Eco-Worthy rack batteries expose Pylon-style CAN telemetry. The most important charging constraints are:

| Signal | Meaning | Supervisor use |
|---|---|---|
| CVL | Charge voltage limit advertised by the BMS | Compare against Classic absorb, float, equalize, and max temp-comp voltage settings |
| CCL | Charge current limit advertised by the BMS | Input to the charge allocator's closed-loop current budget |
| Charge enable | Whether the BMS currently permits charge | Display and future alert/control input |
| Protections/alarms | BMS fault and warning state | Display as battery `Protection/Alarms`; any non-nominal value is significant |
| Min/max cell voltage | Cell-level top-of-charge behavior | Guardrail input to the charge allocator |
| Cell voltage delta | Difference between highest and lowest reported cell | Guardrail input to the charge allocator near the top knee |

The BMS can change CCL dynamically. During the June 2, 2026 top-off observation, the BMS advertised 200 A through most of the charge, stepped down to 100 A near the top, and later stepped down to 40 A while max-cell voltage and cell delta rose. Actual battery charge current was far below these advertised limits, so the CCL reduction was interpreted as an advisory/taper signal rather than an immediate current constraint.

## Classic Settings Guard

Manual Classic writes should use `scripts/classic-charge-settings.py` instead of ad hoc Modbus snippets. The guarded writer reads the current BMS CVL/CCL before writing and refuses planned settings that exceed the advertised envelope unless `--force` is explicitly supplied.

The guard checks:

- Classic battery current limit must be less than or equal to BMS CCL.
- Classic absorb voltage must be less than or equal to BMS CVL.
- Classic float voltage must be less than or equal to BMS CVL.
- Classic equalize voltage must be less than or equal to BMS CVL.
- Classic max temp-comp voltage must be less than or equal to BMS CVL.

This is a write-time guard only. Because BMS CCL can fall later during taper, the charge allocator continuously adjusts controller current limits to stay inside the current BMS envelope. The supervisor no longer raises a passive warning simply because a controller's configured current limit is higher than the instantaneous BMS CCL.

### Classic Modbus Write Modes

The Classic has two different practical write modes over Modbus TCP:

| Mode | Behavior | Use |
|---|---|---|
| Live / RAM-only | Changes take effect immediately but are lost when the Classic hard power-cycles | Short supervised experiments |
| Persisted / EEPROM | Changes take effect immediately and are saved across Classic hard power-cycles | New baseline settings |

The low-level Ethernet Modbus sequence is:

1. Open a TCP connection to the Classic.
2. Read the Classic serial number from registers `28673` and `28674`.
3. Unlock Ethernet writes by writing those two serial-number words to registers `20492` and `20493`.
4. Write the desired charge-setting registers, such as:
   - `4148`: battery output current limit, scaled by 10.
   - `4149`: absorb voltage, scaled by 10.
   - `4150`: float voltage, scaled by 10.
   - `4151`: equalize voltage, scaled by 10.
   - `4154`: absorb time in seconds.
   - `4155`: maximum temperature-compensated charge voltage, scaled by 10.
5. For a live / RAM-only change, stop here and close the TCP connection.
6. For a persisted / EEPROM change, write `0x0004` to register `4160` to set `ForceEEpromUpdateWriteF`.
7. Read back the charge settings and check that the Classic info flags do not include the EEPROM error bit `0x00000002`.

`scripts/classic-charge-settings.py` implements this sequence. By default it persists changes to EEPROM:

```sh
python scripts/classic-charge-settings.py \
  --classic-host 192.168.0.10 \
  --battery-current-limit 80.0 \
  --absorb-voltage 55.6 \
  --float-voltage 55.0 \
  --equalize-voltage 55.6 \
  --absorb-time 1950 \
  --max-temp-comp-voltage 55.6
```

For a temporary live-only experiment, add `--no-persist`:

```sh
python scripts/classic-charge-settings.py \
  --classic-host 192.168.0.10 \
  --absorb-time 1950 \
  --no-persist
```

Use `--dry-run` to print the planned settings and BMS guard result without writing anything. Use `--force` only when deliberately overriding a CVL/CCL guard refusal.

## Supervisor Status Conditions

Off-normal conditions are reported as **status conditions**. They surface in one
**"Warnings and Faults"** group on every display, are logged in SQLite, and set
the snapshot's overall **severity** — which drives the top-line status and the
`/api/v1/health` verdict. Severity is per-condition, not blanket:

| Condition | Severity | Trigger |
|---|---|---|
| `BMS protection: <flag>` | ERROR | A BMS **protection** flag is set (cell over/under voltage, over/under temperature, charge/discharge over-current, system error) — the BMS has tripped a cutoff |
| `Charge controller 0 CVS exceeds battery CVL` | ERROR | Any Classic charge voltage setpoint > BMS CVL (static config mismatch) |
| `BMS alarm: <flag>` | WARNING | A BMS **alarm** flag is set (the pre-trip "high/low" warnings) |

`status_severity` maps to HTTP on the diagnostic endpoint: ERROR → `/api/v1/health`
`503`, WARNING → `200` (degraded). Liveness `/healthz` stays `200` through all of
these — restarting the supervisor would not clear a battery fault. Device read
failures are folded into the same "Warnings and Faults" group (WARNING).

Current-limit exceedance, high-cell voltage, and high cell delta are handled by
the allocator's closed-loop budget resolver (below) rather than as passive
conditions. (The BMS protection/alarm mapping is the supervisor's *independent*
report of what the BMS itself is signalling, distinct from those allocator
guardrails computed from cell telemetry.)

## Threshold Theory

LiFePO4 cell voltage is relatively flat through the middle of SOC and rises sharply near the top. Because of that, cell voltage delta is state-dependent:

- At mid-SOC, a moderate voltage delta can be noisy or not very meaningful.
- Near the top knee, a rising delta can mean one cell is accepting charge faster than the others or has reached the steep part of the curve earlier.
- Passive balancing is slow. If the charger keeps holding a high pack voltage, the high cell can continue rising while lower cells lag.

The allocator therefore gates delta guardrails on max-cell voltage. Delta is watched more closely only when max cell is in the configured upper-cell zone.

The initial thresholds are based on observed behavior and were later moved from passive alerts into allocator guardrails:

- On June 2, 2026, max cell reached 3.513 V and delta peaked around 68 mV during a supervised elevated-voltage top-off attempt.
- The BMS reported no protections or alarms, and charge enable remained true.
- CCL tapered downward as max-cell voltage and delta rose.
- Rollback reduced charger output and the delta began falling.

The current allocator guardrails are documented in
[`charge-current-allocation.md`](charge-current-allocation.md). As of
2026-06-18, they are max-cell stop at 3.62 V, recovery / upper-zone threshold at
3.55 V, and cell-delta stop at 150 mV in that upper zone.

## Current Control Boundary

The supervisor status layer still reports voltage setpoint violations against
BMS CVL because those are static configuration mismatches. Dynamic current and
cell-voltage conditions are handled in the allocator loop instead:

1. `ChargeCeiling` resolves a net charge allowance from BMS CCL, charge-enable,
   cell guardrails, and low-temperature charge protection.
2. `ChargeCurrentAllocator` distributes that allowance across the controllers.
3. The live supervisor writes the resulting controller current limits and EPEver
   coil state when `--charge-allocation` is enabled.

## Why CCL=0 is a full stop

When the BMS advertises CCL=0 (or clears charge-enable), `ChargeCeiling` returns a
0 allowance and the allocator stops the controllers entirely. The pack then covers
household load from discharge until SOC drops enough that the BMS re-opens CCL, at
which point charging resumes — so the pack naturally cycles just under 100%.

This is deliberate, and the cost is smaller than it looks:

- PV isn't wasted — the arrays simply curtail (there's nowhere to put the harvest
  when the pack is full).
- The load energy drawn during the CCL=0 window is replenished once CCL reopens;
  the only real loss is the battery round-trip (~2–5%) on that sliver, plus
  negligible extra cycling.
- The side effect — not holding the pack hard at 100% — is good for calendar life.

CCL=0 is also **ambiguous**: it can mean "full" (charge-enable still true) or
"charging forbidden — protection/fault" (charge-enable false). Treating it
uniformly as "do not put current into this battery" is the safe default; the
allocation reason distinguishes the two (`"BMS CCL is zero"` vs
`"BMS charge disabled"`).

**Rejected alternative — load-following (net-zero).** Running the controllers to
cover household load at net-zero battery current (PV serves the house; the pack
neither charges nor discharges) would avoid the round-trip. Rejected because the
risk is highest exactly where the margin is thinnest: at a full pack on the steep
knee, a load down-blip momentarily pushes surplus into the battery while CCL=0,
spiking a cell and risking an overvoltage trip. The efficiency gain (~2–5% on a
sliver) isn't worth the trip risk and the added control complexity. The genuinely
useful surplus-handling option is a **diversion/dump load** (e.g. water heat) —
additive hardware, not a loosening of the cutoff.

### The Classic can't be hard-disabled (known behavior)

The EPEver gets a true off at CCL=0 (its charge-enable coil), but the Classic has
no Modbus disable — the allocator's only lever is its output current-limit
register, written to 0. The Classic does **not** honor 0 A as a shutoff: it floors
at a small output and short-cycles Bulk↔Resting, emitting a benign trickle
(observed up to ~1.6 A / tens of watts, hunting). That trickle serves household
load, not battery charge — the pack stays net-discharging at CCL=0 — so it is
harmless in normal operation. The only edge case is household load momentarily
dropping below a burst, briefly leaking ~1 A into a full pack (well within BMS
tolerance). Status quo accepted; it is just cosmetically noisy in the stage
telemetry.

A genuine hard-stop path is held **in reserve**: the Classic's **Aux 1** can be
configured as an **input** that gates charging — effectively a remote
charge-enable, the Classic's analogue to the EPEver coil — driven by the
supervisor (e.g. a Pi GPIO line). Aux 1 is currently occupied by the **Whizbang
Jr** battery-current shunt, which the BMS's own current/SOC telemetry has
effectively obsoleted. Retiring the WBjr would free Aux 1 to give the Classic a
true supervisor-commanded on/off at CCL=0 — a future development, not currently
planned.

## Cold-Weather Operation: VOC as Irradiance Proxy and the Battery Heater Heuristic

LiFePO4 cells cannot safely accept charge below 0 °C. The `ChargeCeiling`
low-temperature stop latches all charging at or below 0 °C pack temperature and
holds the latch until recovery to 2 °C. A 200 W ceramic heater in the battery
compartment can bring the pack above the recovery threshold and restore charging
within minutes of sunrise on a good solar day — but running the heater when
there is no solar income can drain the pack further.

### What signals irradiance while charging is disabled?

When pack temperature is below 0 °C, the low-temperature stop is active and the
BMS charge-enable is false. The EPEver coil is open; the Classic floor-trickles
into the load bus. Neither charger's output current is a meaningful irradiance
signal in this state.

**The Classic's `last_voc` register (4122) continues to update every ~60 seconds
regardless of whether charging is active.** The Classic performs open-circuit
voltage (VOC) sweeps internally as part of its MPPT tracking algorithm and
records the result to this register continuously — it is not gated by charge
stage, CCL, or the allocator's current-limit register. This was confirmed
empirically on 2026-06-26: with Classic commanded to 0 A via the allocation
override API, `last_voc` updated every minute and tracked cloud cover in real
time (131.4 → 131.2 → 131.0 → 129.8 V over eight minutes).

Additionally, `pv_voltage` (register 4116) — the real-time PV bus voltage — rises
toward VOC whenever the Classic is at or near 0 A output, making it a second
real-time proxy for the same quantity during charge-disabled periods.

### VOC–irradiance relationship after the array wiring correction

VOC follows a logarithmic relationship with irradiance and also rises as module
temperature falls. The original 130–134 V experiment was performed while array
0 was unknowingly wired as mismatched 4s∥3s strings. It must not be used with
the corrected 4s2p topology.

Post-correction Bulk telemetry from 2026-07-19 onward produced these raw summer
results:

| `last_voc` threshold | Samples ≥ 200 W | Samples ≥ 400 W |
|---:|---:|---:|
| 164 V | 91.3% | 66.9% |
| 166 V | 97.6% | 76.1% |
| 168 V | 99.5% | 86.6% |
| 170 V | 99.8% | 95.8% |

A fixed threshold is still unsafe as a year-round irradiance proxy because the
modules' −0.34%/°C VOC coefficient lets weak cold-weather light reach voltages
that would imply much stronger light in summer. The production loop therefore
normalizes VOC to 25 °C using the existing local ambient-temperature probe:

```text
normalized_VOC = measured_VOC / (1 + 0.0034 × (25 − local_ambient_temperature_C))
```

Normalized VOC ≥ 158 V corresponded to ≥ 200 W in 79.1% and ≥ 400 W in
56.4% of post-correction Bulk samples, with 639 W average output. The threshold
admits both verified 2026-08-10 conditions: 166.8 V VOC at 19.3 °C normalized
to 163.6 V while the array produced about 1.6 kW, and 161.8 V at 19.4 °C
normalized to 158.8 V while it produced 862 W. Reactive heating turns on after
the condition holds continuously for 60 seconds and turns off below 154 V
normalized VOC.

### Implemented heater heuristic

Run the 200 W ceramic heater when **all** of the following hold:

1. Minimum pack temperature is below 2 °C.
2. Locally temperature-normalized Classic `last_voc` is at least 158 V continuously
   for 60 seconds.
3. The local ambient-temperature probe is available.

The relay remains on until minimum pack temperature exceeds 5 °C or normalized
VOC falls below 154 V. Missing battery, Classic, or local ambient-temperature
telemetry fails reactive heating off. There is no internet/weather-service
dependency. The EPEver cannot serve as a corroborating signal because it is
disabled during the sub-zero charge-inhibit state.

### Note on the Classic 0 A floor during charge-disabled periods

As noted in the section above, the Classic does not honor 0 A as a true shutoff:
it trickles up to a few tens of watts into the load bus even when the allocator
has written 0 to its current-limit register. During sub-zero pack temperatures
this trickle feeds household load rather than the battery (the BMS charge-enable
is false, so the battery absorbs no charge), and is harmless. It does not
meaningfully reduce the load that a heater decision needs to account for.
