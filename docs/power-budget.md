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
| Array 0 (2.4 kW rooftop) | 1.27 measured; **~1.45 expected going forward** | measured with a wiring fault (below); corrected 2026-07-18 |
| Array 1 (3.6 kW, flat on ground) | 0.57 (~16% of nameplate) | flat + tight tree horizon + vegetation; expected to recover substantially when mounted — re-measure |
| Load, June occupancy | 5.15 kWh/day (214 W avg) | DC-bus basis |
| Overnight load floor | ~95 W | Starlink gen 2 ~34 W DC-side + Magnum no-load ~44 W + Pi/comms ~15 W |
| Refrigeration share | **~0.77 kWh/day (32 W average)** | dedicated combined-branch meter, 2026-07-10..20; two cube freezers, capacity-normalized compressor duty ~27% |
| Bank usable | ~8.7 kWh | 2× Cubix 100 (10.24 kWh nominal) at 85% |

The dedicated refrigeration measurement supersedes the original ~2.6 kWh/day
allocation inferred before the S31 was installed. It does **not** change the
5.15 kWh/day full-occupancy baseline, which was measured at the DC bus, but it
does mean that the modeled “no refrigeration” cases below subtract too much
load—about 1.8 kWh/day if the July utilization applies. Treat their favorable
results as stale pending a scenario rerun. See
[Individual Load Metering](subsystems/load-metering.md#measured-refrigeration-utilization-2026-07-10-through-2026-07-20)
for the tier and duty-cycle analysis.

**Array 0 wiring-fault correction (discovered 2026-07-18, ~13:45):** during
the entire calibration window one array 0 panel was disconnected and the
remaining seven were wired 4s ∥ 3s instead of the intended 4s2p. With a 3s
string (Voc ~135 V) paralleled onto a 4s string (Vmp ~144 V), the Classic's
global MPP sits near ~108 V where both strings conduct — delivering
essentially 7 panels' worth, i.e. ~7/8 of intended output. The measured 1.27
therefore characterizes the *faulted* array; with the wiring corrected,
array 0 should run ≈ 1.27 × 8/7 ≈ **1.45 W per W/m²** (~60% of nameplate).
Scenario numbers below use the corrected value; the post-fix recalibration
(open measurements) confirms it.

## Array 1 status (updated 2026-07-18)

**Array 1 is decommissioned until mount construction completes, expected
~September 2027.** Planned final configuration: 3s4p (unchanged), 45° tilt,
on a **cabin rooftop** — same footprint as the ground dry run but
~10 ft higher, with slightly reduced shading expected from the added height.
Until then the system runs on array 0 alone (see the interim scenario table
below). The AR shading survey below was taken at the old ground-platform
first-tier height; its December beam figure is now a conservative floor —
re-survey from the rooftop mounting height when access allows.

## Site solar geometry (array 1 site)

AR winter-solstice-path survey from the ground-platform first-tier panel
position (2026-07-02): morning 9:30–11:30 crosses mixed trees (deciduous,
~30–40% leaf-off beam transmission, one conifer spire at ~11:00);
**12:30–14:30 is blocked by the building roofline** — the richest beam hours;
15:00+ is trees/horizon. Energy-weighted December direct-beam passage ≈
**15%** at that height.

Key facts: solstice noon sun elevation here is 18.7° (25° by mid-February, so
February largely self-recovers). At 45° tilt, late-December plane-of-array
resource is ~1.58 kWh/m²/day — ~0.51 diffuse + ~1.08 beam — versus
0.87 horizontal, i.e. two-thirds of the tilt gain is beam, which is what the
roofline takes. No setback margin exists (dense trees behind the platform);
recovery, if any, comes from upper rows (~0.7 m/row at 45°). Follow-up: repeat
the AR shot at +1.4 m and +2.1 m. This geometry is why the target wiring for
the mounted array is 3s4p with one string per row — so a partly-cleared array
degrades row-proportionally rather than collapsing (rationale and electrical
envelope in the charge-controller doc).

## Annual balance and operating modes

Weekly-average PV potential vs load across the year (mean of 10 years):
surplus roughly March through September, structural deficit mid-October
through February, bottoming in the Dec 20 – Jan 1 weeks.

### Interim: array 0 only (until array 1 remount, ~Sep 2027)

With array 1 decommissioned, winter 2026–27 runs on array 0 alone
(corrected coefficient 1.45; darkest-week potential ~1.5 kWh/day):

| Mode | Worst 7-day (median winter) | Median winter deficit | Generator sessions/winter |
|---|---|---|---|
| Full occupancy | −32 kWh | ~550 kWh | ~46 — generator is a routine appliance |
| No refrigeration *(legacy 2.6 kWh/day subtraction; rerun required)* | −12 kWh | ~116 kWh | ~10 — not bank-survivable unattended |
| Lean unattended caretaker | **+0.8 kWh** (−1.1 worst of 10) | ~5 kWh | 0 — survives every winter at nominal heater duty |

The lean caretaker verdict carries the load: **unattended winter 2026–27 is
viable on array 0 alone**, with min SOC ≥ 76% at nominal heater duty — but
the margin is thin against heater-duty error (1.5× duty produces a handful of
dead days; 2× is structural). This winter *is* the heater-duty measurement
winter; instrument it before leaving the site unattended.

### Post-mount scenarios (array 1 on the cabin roof, ~Sep 2027+)

Scenario results with array 1 mounted (45°, December beam 15% — conservative
now that the roof adds ~10 ft). Because mounted array 1's
real-world health is unknown until it is on the platform, its **performance
ratio (PR)** — delivered output as a fraction of nameplate under the same
irradiance, absorbing wiring, mismatch, soiling, and conversion losses — is
bracketed: PR 0.55 pessimistic (performs like aging array 0 does today) to
PR 0.70 optimistic (mounting recovers most of the flat-layout losses). The
post-mount recalibration below replaces this bracket with a measurement.

| Mode | Load | Darkest-week balance | Winter outcome (9 simulated winters) |
|---|---|---|---|
| Full occupancy | ~5.7 kWh/day incl. heater | −2.2 to −2.7 kWh/day | Structural deficit, ~165–215 kWh/winter; generator required (~13–17 bank-empty events/winter) |
| No refrigeration *(legacy 2.6 kWh/day subtraction; rerun required)* | ~2.3 + heater | ≈ −0.3 kWh/day | Bank rides through in **all 9 winters at both bracket ends**: worst-winter minimum SOC 24% at PR 0.55, 43% at PR 0.70 |
| Lean unattended caretaker | ~0.5 + heater | positive to neutral | Robust in every scenario tested, incl. zero December beam and 2× heater duty; min SOC ≥ 76% even with array 1 contributing nothing |

Lean caretaker stack: Pi + comms ~15 W continuous; inverter hard-off except a
daily ~60-minute window (supervisor toggles the Magnum — the one Magnum write
that works; Starlink is the only AC load) costing ~0.1 kWh/day; heater. The
comms window is a rounding error — **the battery heater dominates the winter
budget** (0.5–1.9 kWh/day depending on temperature) and its actual duty cycle
is the controlling unknown of the whole model.

Two different loads dominate two different questions. The heater is the
largest *unavoidable* draw — it sets what the lean stack must spend. Among
*discretionary* loads, always-on Starlink is the largest (~0.8+ kWh/day,
more when snow-melting) — roughly a third of the darkest-week PV harvest —
which is why lean mode reduces it to the daily window (~1/8 the cost) rather
than carrying it continuously.

## Battery capacity: 200 Ah vs 400 Ah (evaluated 2026-07-02)

Question: is doubling the bank (~8.7 → ~17.4 kWh usable) game-changing in any
off-season scenario? **No — winter is energy-limited, not storage-limited.**
A battery bridges deficits; it cannot erase a structural one.

| Scenario | 200 Ah | 400 Ah | Verdict |
|---|---|---|---|
| Full load, occupied | ~13–17 generator sessions/winter | ~7–9 | Halves generator *starts*; same total energy |
| No-fridge, occupied *(legacy subtraction; rerun required)* | 0 bank-empty events in 9 winters, both PR bracket ends | 0 | Previous result; measured refrigeration share invalidates this load allocation |
| Lean unattended, as-built | 0 failures (even 2× heater duty) | 0 | Already solved at 200 Ah |
| Lean unattended + array 1 failed + 1.5× heater | 4 dead days in 9 winters | 0 | Was 37 vs 6 before the array 0 wiring-fault correction; the corrected array largely closes this corner on its own |
| Same double fault at 2× heater | 98 dead days | 38 | Structural; neither bank size saves it |

The marginal value of doubling is (a) fewer, longer generator sessions when
occupied at full load, and (b) residual insurance depth in the *double-fault*
unattended winter (an array failure combined with underestimated heater duty)
— a corner the array 0 wiring-fault correction already shrank from 37 dead
days to 4. The same budget pointed at winter *supply* — unshaded winter panel
capacity, or platform height that clears the roofline — attacks the deficit
directly and is worth more per dollar in every non-fault scenario.

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
  allocator adapts automatically. The load/consumption math uses the
  operator-set `BATTERY_CAPACITY_AH` in `/etc/offgrid-power.env` — update it
  when packs are added or removed.

## Array 2 sizing (provisional, EPEver PV2 — sketched 2026-07-02)

A future array 2 at a different site would land on the EPEver's reserved PV2
input (INDE mode, its own MPPT channel). Electrical envelope for CS6X-class
(~45 V Voc, 72-cell) modules:

- **Series: 4s is the ceiling and the choice.** 4s cold Voc ~220 V vs the
  250 V limit (safe to ~−90 °C); 5s sits at the 225 V/25 °C limit with zero
  margin and crosses 250 V at ~−8 °C. (For reference, the same arithmetic
  limits the lost Victron 150/85 to 2s at this climate — any 3s+ string
  exceeds its 145/150 V limits on ordinary winter mornings.)
- **Parallel: 5 legs is the spec ceiling** (5 × 8.9 A Isc = 44.4 A vs the
  50 A per-input limit; 6 legs is over). Per-string 15 A fuses required
  beyond 2 legs. At 5 legs the feeder design current is ~69 A (Isc × 1.56) —
  size conductors accordingly.
- **Power sharing:** the controller's 100 A battery-side ceiling (~5.2 kW) is
  shared across both inputs; array 1 already brings 3.6 kW of nameplate.
  Overpaneling past the ceiling is correct for a winter-driven system
  (December output runs 15–20% of nameplate; clipping only occurs in
  curtailment season), but the marginal *summer* value of legs beyond ~2 is
  zero.

Winter value, assuming a clear solstice sightline at 45° tilt (the point of
picking a new site): each 4s leg (~1.2 kW) ≈ **1.2–1.3 kWh/day in the darkest
weeks**. **4s4p (16 panels, 4.8 kW) stacked on the as-built system clears the
full-occupancy winter load (~5.7 kWh/day) with margin in a mean year — the
configuration that retires the generator at full load**, which no storage
purchase can do (see the battery section above). The fifth leg buys
worst-of-decade overcast margin at the cost of a heavier feeder.

Site selection is the whole game: an AR solstice-path survey at the candidate
site *before* trenching (the array 1 lesson) is worth more than the fifth
leg. December beam passage is the number that decides whether a leg delivers
1.3 kWh/day or its diffuse-only floor (~0.4).

## Open measurements

In rough order of information value:

1. **Battery heater duty vs temperature** (winter 2026–27; heater is
   Pi-permissive, so log it). With the interim system on array 0 alone, the
   lean-caretaker margin is thin against heater-duty error — this
   measurement is now urgent, not just informative, and it retires the
   model's biggest unknown.
2. **Post-fix recalibration of array 0** (`calibrate_pv.py 2026-07-19` after
   a week of corrected 4s2p wiring; scheduled 2026-07-26): expect the slope
   near 1.45; a materially lower result means the array has losses beyond
   the wiring fault, and the scenario numbers revert toward the
   pre-correction column. Array 1 is decommissioned, so its slope should
   read ~0/absent in this run.
3. **Seasonal refrigeration utilization:** repeat the combined-tier analysis
   in winter and after any thermostat-probe/thermal-mass change. This tests
   whether the July ~0.77 kWh/day and short refrigerator cycles carry into
   the conditions used by the annual model, then supports a corrected
   no-refrigeration scenario.
4. **AR solstice-path survey from the cabin rooftop mounting height**
   (whenever roof access exists, ideally near solstice-relevant sun angles):
   replaces the old +1.4/+2.1 m ladder follow-ups; quantifies how much of
   the December noon block the ~10 ft of extra height recovers.
5. *(Deferred to ~Sep 2027, after remount)* Post-mount recalibration of
   array 1 (`calibrate_pv.py <date>`): does per-kW effectiveness recover
   from the 16% flat floor toward the PR 0.55–0.70 band? Verify
   `epever.1 pv_voltage` ~108 V class (3s4p) on reconnect, and capture a
   clear-day December `pv_power` trace — a midday notch vs a clean bell is
   the measured occlusion profile.

## History

- 2026-07-02 — initial model, AR shading survey, 3s4p decision, battery
  200-vs-400 Ah analysis ([journal](journal/2026-07-02.md)).
- 2026-07-18 — array 0 wiring fault discovered and corrected (one panel
  disconnected, 4s ∥ 3s during the whole calibration window); array 0
  coefficient revised 1.27 → ~1.45 going forward and all scenario numbers
  re-run ([journal](journal/2026-07-18.md)).
- 2026-07-18 — array 1 decommissioned for mount construction, expected
  complete ~Sep 2027; final plan 3s4p at 45° on a cabin rooftop (~10 ft
  higher than the surveyed ground position, slightly reduced shading).
  Interim array-0-only scenario table added; unattended winter 2026–27
  remains viable on array 0 alone.
- 2026-07-20 — dedicated refrigeration trace measured ~0.77 kWh/day and
  27% capacity-normalized compressor duty; the earlier 2.6 kWh/day allocation
  and derived no-refrigeration scenarios were marked stale
  ([journal](journal/2026-07-20.md)).
