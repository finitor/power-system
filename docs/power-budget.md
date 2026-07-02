# Power Budget

Measured load, calibrated PV production, and the annual power-balance model —
the canonical reference for "will the system run net positive, and when."
First built 2026-07-02 from the initial ~13 days of full telemetry; update the
calibration and scenario numbers as real data replaces estimates (see
[Open measurements](#open-measurements)).

Model scripts: [`scripts/calibrate_pv.py`](../scripts/calibrate_pv.py) (runs
on the Pi against the metrics DB) and
[`scripts/annual_model.py`](../scripts/annual_model.py) (runs anywhere against
downloaded irradiance; fetch commands in its docstring). Decision context for
the array 1 wiring that fell out of this analysis:
[charge-controller doc](subsystems/charge-controller.md#array-1-string-topology-3s4p-decided-2026-07-02).

## Method

1. **PV effectiveness** is calibrated from *uncurtailed* telemetry intervals
   only: 10-minute bins where the system is in Bulk with SOC ≤ 88%, so
   production is sun-limited rather than demand-limited. Summer daily energy
   counters are useless for potential — the bank hits 100% SOC almost every
   summer day and the allocator throttles the arrays (measured June
   curtailment: ~5.5 kWh/day delivered out of ~14 kWh/day potential).
2. Delivered charge power in those bins is regressed against irradiance from
   the supervisor's Open-Meteo weather feed, giving **W of DC per W/m²** per
   array. This coefficient absorbs orientation, shading, soiling, wiring, and
   conversion losses as-built.
3. The coefficient is scaled across the year by a 10-year (2016–2025)
   Open-Meteo/ERA5 daily irradiance series for the site grid cell
   (47.952, −84.841) — GHI for the as-is geometry, 45°-south plane-of-array
   (split into beam + diffuse components) for mounted-array scenarios — with
   a cell-temperature correction (~+13% in deep winter vs the June
   calibration).
4. Load comes from the supervisor's `load` source (DC-bus balance: controller
   output minus battery net; includes inverter losses), plus a 200 W battery
   heater modeled at a temperature-scaled duty (≈0 above +2 °C, ~4 h/day at
   −10 °C, ~8 h/day at −20 °C — **estimate, not yet measured**).
5. Scenarios are evaluated two ways: weekly net balance (10-year mean and
   worst-of-record), and a daily battery SOC simulation through each of the
   nine complete winters (Oct 1 – Apr 30, bank starting full).

## Calibration baseline (2026-06-20 .. 2026-07-02)

| Quantity | Value | Notes |
|---|---|---|
| System PV effectiveness | 1.84 W per W/m² GHI | 261 uncurtailed bins; BMS charge limit (~10.6 kW) never binding |
| Array 0 (2.4 kW rooftop) | 1.27 (~53% of nameplate) | normal for an older fixed array |
| Array 1 (3.6 kW, flat on ground) | 0.57 (~16% of nameplate) | flat + tight tree horizon + vegetation; expected to recover substantially when mounted — re-measure |
| Load, June occupancy | 5.15 kWh/day (214 W avg) | DC-bus basis |
| Overnight load floor | ~95 W | Starlink gen 2 ~34 W DC-side + Magnum no-load ~44 W + Pi/comms ~15 W |
| Refrigeration share | ~2.6 kWh/day | duty-cycled ~100–120 W average |
| Bank usable | ~8.7 kWh | 2× Cubix 100 (10.24 kWh nominal) at 85% |

Telemetry gotchas encountered: `epever.1 generated_today` is a cumulative
counter, not daily; query the live DB with `mode=ro`, never `immutable=1`
(spurious "malformed image" errors mid-scan while the supervisor writes).

## Site solar geometry (array 1 platform)

AR winter-solstice-path survey from the exact first-tier panel position
(2026-07-02): morning 9:30–11:30 crosses mixed trees (deciduous, ~30–40%
leaf-off beam transmission, one conifer spire at ~11:00); **12:30–14:30 is
blocked by the building roofline** — the richest beam hours; 15:00+ is
trees/horizon. Energy-weighted December direct-beam passage ≈ **15%** at
first-tier height.

Key facts: solstice noon sun elevation here is 18.7° (25° by mid-February, so
February largely self-recovers). At 45° tilt, late-December plane-of-array
resource is ~1.58 kWh/m²/day — ~0.51 diffuse + ~1.08 beam — versus
0.87 horizontal, i.e. two-thirds of the tilt gain is beam, which is what the
roofline takes. No setback margin exists (dense trees behind the platform);
recovery, if any, comes from upper rows (~0.7 m/row at 45°). Follow-up: repeat
the AR shot at +1.4 m and +2.1 m. The 3s4p row-per-string wiring exists so a
partly-cleared array degrades row-proportionally (see the charge-controller
doc).

## Annual balance and operating modes

Weekly-average PV potential vs load across the year (mean of 10 years):
surplus roughly March through September, structural deficit mid-October
through February, bottoming in the Dec 20 – Jan 1 weeks. Scenario results,
as-built (array 1 mounted 45° S, December beam 15%, intrinsic performance
ratio bracketed 0.55–0.70):

| Mode | Load | Darkest-week balance | Winter outcome (9 simulated winters) |
|---|---|---|---|
| Full occupancy | ~5.7 kWh/day incl. heater | −2.5 to −3 kWh/day | Structural deficit, ~140–290 kWh/winter; generator required (~14–19 bank-empty events/winter) |
| No refrigeration | ~2.3 + heater | ≈ −0.5 kWh/day | Bank rides through: worst corner shows one 0.8 kWh shortfall in 9 winters; PR 0.70 never empties (min SOC 38% worst winter) |
| Lean unattended caretaker | ~0.5 + heater | positive to neutral | Robust in every scenario tested, incl. zero December beam and 2× heater duty; min SOC ≥ 64% even with array 1 contributing nothing |

Lean caretaker stack: Pi + comms ~15 W continuous; inverter hard-off except a
daily ~60-minute window (supervisor toggles the Magnum — the one Magnum write
that works; Starlink is the only AC load) costing ~0.1 kWh/day; heater. The
comms window is a rounding error — **the battery heater dominates the winter
budget** (0.5–1.9 kWh/day depending on temperature) and its actual duty cycle
is the controlling unknown of the whole model.

Load context for net-zero framing: an always-on Starlink alone
(~0.8+ kWh/day, more when snow-melting) consumes most of the darkest-week
budget; a 60-minute daily window costs ~1/8 of that.

## Battery capacity: 200 Ah vs 400 Ah (evaluated 2026-07-02)

Question: is doubling the bank (~8.7 → ~17.4 kWh usable) game-changing in any
off-season scenario? **No — winter is energy-limited, not storage-limited.**
A battery bridges deficits; it cannot erase a structural one.

| Scenario | 200 Ah | 400 Ah | Verdict |
|---|---|---|---|
| Full load, occupied | ~14–19 generator sessions/winter | ~7–10 | Halves generator *starts*; same total energy |
| No-fridge, occupied | 1 bank-empty event in 9 winters (worst corner) | 0 | Already solved at 200 Ah |
| Lean unattended, as-built | 0 failures (even 2× heater duty) | 0 | Already solved at 200 Ah |
| Lean unattended + array 1 failed + 1.5× heater | 37 dead days, runs to 13 days (cold-lock risk) | 6 dead days, runs ≤ 3 | The one outcome-class change |
| Same double fault at 2× heater | 183 dead days | 97 | Structural; nothing saves it |

The marginal value of doubling is (a) fewer, longer generator sessions when
occupied at full load, and (b) insurance depth against the *double-fault*
unattended winter (an array failure combined with underestimated heater duty).
The same budget pointed at winter *supply* — unshaded winter panel capacity,
or platform height that clears the roofline — attacks the deficit directly
and is worth more per dollar in every non-fault scenario.

**Decision (2026-07-02): collect a full season of data first** (heater duty,
post-mount calibration), then choose between insurance (battery), supply
(panels), or neither.

### Mixing battery ages in the bank

Waiting does not create a battery-mixing problem. The "never mix ages" rule is
a lead-acid *series-string* inheritance, where the weakest cell caps the
string and gets reverse-charged at the bottom. This bank is the opposite
topology: self-contained 48 V LiFePO4 rack packs in **parallel**, each with
its own BMS. An older parallel pack settles at the same bus voltage and simply
contributes proportionally less current — self-limiting, not damaging. LFP
calendar fade is ~2%/year at moderate temperatures, and a season of this
system's duty (shallow cycles at ≤0.3C) is trivial cycle wear.

Caveats to honor at expansion time:

- Same model if available (matched voltage curves and CAN behavior). Product
  availability is the only real argument for buying early.
- Charge all packs full separately before paralleling (no SOC delta at
  connection).
- Symmetric cabling / bus bar — lead resistance skews current sharing far
  more than a year of age.
- Bank capacity and CCL flow live from the BMS aggregate, so the charge
  allocator adapts automatically; but check the hardcoded 200 Ah default in
  `LoadTotalsTracker`
  ([load.py](../software/pi-controller/src/offgrid_power/load.py)).

## Open measurements

In rough order of information value:

1. **Battery heater duty vs temperature** (first winter; heater is
   Pi-permissive, so log it). The 2× heater sensitivity is the failure mode of
   unattended winters — this measurement retires the model's biggest unknown.
2. **Post-mount recalibration** of array 1 (`calibrate_pv.py <date>` after a
   week of telemetry): does per-kW effectiveness recover from the 16% flat
   floor toward the PR 0.55–0.70 band?
3. **AR solstice-path screenshots at +1.4 m and +2.1 m** (upper-row
   sightlines over the roofline).
4. **Clear-day December `epever.1 pv_power` trace**: a midday notch vs a
   clean bell is the roofline occlusion profile, measured — replaces the AR
   estimate outright.
5. Post-rewire check (3s4p, dry-run 2026-07-02): `epever.1 pv_voltage`
   ~108 V class instead of ~144 V.

## History

- 2026-07-02 — initial model, AR shading survey, 3s4p decision, battery
  200-vs-400 Ah analysis ([journal](journal/2026-07-02.md)).
