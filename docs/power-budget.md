# Power Budget

Measured load, calibrated PV production, and the annual power-balance model —
the canonical reference for "will the system run net positive, and when."
First built 2026-07-02 from the initial ~13 days of full telemetry; update the
calibration and scenario numbers as real data replaces estimates (see
[Open measurements](#open-measurements)).

Model scripts: [`scripts/calibrate_pv.py`](../scripts/calibrate_pv.py) (runs
on the Pi against the metrics DB) and
[`scripts/annual_model.py`](../scripts/annual_model.py) (runs anywhere; fetches
and caches its own irradiance data). Decision context for
the array 1 wiring that fell out of this analysis:
[charge-controller doc](subsystems/charge-controller.md#array-1-string-topology-3s4p-decided-2026-07-02).

## Method

1. **PV effectiveness** is calibrated from *uncurtailed* telemetry intervals
   only: 10-minute bins where the system is in Bulk with SOC ≤ 88%, so
   production is sun-limited rather than demand-limited. Summer daily energy
   counters are useless for potential — the bank hits 100% SOC almost every
   summer day and the allocator throttles the arrays (measured June
   curtailment: ~5.5 kWh/day delivered out of ~14 kWh/day potential).
2. Delivered charge power in those bins is regressed against **plane-of-array
   (POA) irradiance**, reconstructed from the weather feed's direct-normal and
   diffuse components using each array's actual tilt and azimuth, and
   normalized to a 25 °C cell temperature. The resulting coefficient (**W of
   DC per W/m² of POA at 25 °C**) absorbs shading, soiling, wiring, and
   conversion losses, but *not* geometry — which is now modeled explicitly.
   Regressing against horizontal irradiance (GHI) instead is a serious error
   for a tilted array; see [Why GHI-proportional was
   wrong](#why-ghi-proportional-modeling-was-wrong-corrected-2026-07-27).
3. The coefficient is scaled across the year by a 10-year (2016–2025)
   **explicitly pinned ERA5** series for the site grid cell: hourly 45°-south
   plane-of-array irradiance (beam + diffuse) and ambient temperature. Pinning
   `models=era5` avoids Open-Meteo's default best-match series, which mixes
   weather products across years and is unsuitable for worst-winter ranking.
4. Load comes from the supervisor's `load` source (DC-bus balance: controller
   output minus battery net; includes inverter losses), plus a 200 W battery
   heater modeled at a temperature-scaled duty (≈0 above +2 °C, ~4 h/day at
   −10 °C, ~8 h/day at −20 °C — **estimate, not yet measured**).
5. Scenarios are evaluated two ways: daily/weekly energy balance, and an
   **hourly** battery SOC simulation through each of the nine complete winters
   (Oct 1 – Apr 30, bank starting full). Hourly resolution preserves midday
   battery saturation and overnight draw instead of allowing discarded noon
   surplus to pay an evening load.
6. Attended scenarios model the **manual 3.2 kW generator** starting at 20% SOC
   (an explicit, configurable planning assumption) and running until the
   operator-observed SOC reaches **90%**. The 3.2 kW is currently treated as
   DC-bus-equivalent output because generator-to-battery conversion loss has
   not been measured; modeled runtime is therefore optimistic by that unknown
   loss. Unattended scenarios never start the generator.

## Calibration baseline (2026-06-20 .. 2026-07-02; array 0 refit 2026-07-19 .. 07-30)

| Quantity | Value | Notes |
|---|---|---|
| Array 0 geometry | **45° tilt, 180° azimuth (due south), 30 ft above grade** | rooftop; recorded 2026-07-27. Same geometry as planned array 1 |
| Array 0 (2.4 kW) effectiveness | **1.60–2.01 W per W/m² POA at 25 °C** (67–84% of nameplate); 14-day fit through 2026-08-01 **1.70** | bracket, not a point estimate — see [calibration limits](#why-the-coefficient-is-not-locked-in) |
| Array 1 (3.6 kW, flat on ground) | 0.57 W per W/m² GHI (~16% of nameplate) | superseded: array 1 decommissioned 2026-07-18, and this figure predates the POA reformulation. Re-measure after remount |
| Load, June occupancy | 5.15 kWh/day (214 W avg) | DC-bus basis; July 10–28 mean 217 W |
| Overnight load (01:00–04:00) | **~184 W mean** (105 W instantaneous minimum) | of which refrigeration ~32 W. Identified always-on gear — Starlink ~34 W DC-side, Magnum no-load ~44 W, Pi/comms ~15 W — accounts for only ~93 W, leaving **~59 W unidentified** |
| Refrigeration share | **~0.77 kWh/day (32 W average)** | dedicated combined-branch meter, 2026-07-10..20; two cube freezers, capacity-normalized compressor duty ~27% |
| Bank usable | ~8.7 kWh | 2× Cubix 100 (10.24 kWh nominal) at 85% |

The dedicated refrigeration measurement supersedes the original ~2.6 kWh/day
allocation inferred before the S31 was installed. It does **not** change the
5.15 kWh/day full-occupancy baseline, which was measured at the DC bus. The
"no refrigeration" scenarios have been rerun at the corrected **4.38 kWh/day**
(5.15 − 0.77). See
[Individual Load Metering](subsystems/load-metering.md#measured-refrigeration-utilization-2026-07-10-through-2026-07-20)
for the tier and duty-cycle analysis.

**Why the original 2.6 kWh/day estimate was ~4× too high** (worth recording so
the mistake is not repeated for the next unmetered load): it was inferred by
subtracting an assumed baseline from the daily mean, and both halves were
wrong. First, the baseline used the overnight *minimum* (~95–105 W) — the
instantaneous trough when the compressor is off and nothing else happens to be
drawing — rather than the overnight *mean* of ~184 W. Second, the entire
remaining gap was attributed to refrigeration, when most of it is ordinary
daytime activity. The metered reality: refrigeration is nearly **flat at
29–35 W in every hour of the day**, while total load swings from 183 W
overnight to 274 W at midday. The diurnal shape that was assumed to be
compressor duty cycling is human activity; the freezers contribute almost none
of it. Refrigeration is 14.7% of total load, not the ~50% assumed.

Because 0.77 kWh/day is a July figure, winter refrigeration in a cool space
should be *lower* still — making load-shedding the freezers an even weaker
winter strategy than the scenario tables show.

**Array 0 wiring-fault correction (discovered 2026-07-18, ~13:45):** during
the entire calibration window one array 0 panel was disconnected and the
remaining seven were wired 4s ∥ 3s instead of the intended 4s2p. With a 3s
string (Voc ~135 V) paralleled onto a 4s string (Vmp ~144 V), the Classic's
global MPP sits near ~108 V where both strings conduct — delivering
essentially 7 panels' worth, i.e. ~7/8 of intended output. The pre-fix
measurement therefore characterizes the *faulted* array.

The post-fix recalibration (2026-07-27, nine days of corrected wiring) did
**not** cleanly confirm the projected 8/7 recovery: measured output rose ~40%
rather than the predicted 14%. Part of that is likely real — a real MPPT
facing two local maxima can hunt and settle on the wrong one, so the true
fault cost was probably worse than the idealized 7/8 — but the windows also
disagree by more than the fault explains, which is what
[calibration limits](#why-the-coefficient-is-not-locked-in) is about. The
model now carries a bracket rather than a corrected point estimate.

## Why GHI-proportional modeling was wrong (corrected 2026-07-27)

Until 2026-07-27 array 0 was modeled as producing in proportion to
**horizontal** irradiance (GHI). Its actual geometry — 45° tilt, due
south — was undocumented. That is a large, seasonal, one-directional error,
because the ratio of 45°-south plane-of-array irradiance to horizontal
irradiance varies by 2× across the year:

| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| POA(45°S)/GHI | 1.80 | 1.50 | 1.30 | 1.08 | 0.95 | 0.90 | 0.93 | 1.03 | 1.23 | 1.44 | 1.65 | **1.86** |

A coefficient fitted in July (ratio 0.93) and applied to December (ratio 1.86)
**understates December output by ~2×**. This is geometry, not a fitted
parameter, so the correction holds regardless of the coefficient's exact
value. It is also why the 45° tilt is the right choice at this latitude: the
panel normal sits 45° above the horizon, which is within ~26° of the solstice
noon sun (18.7°) and within ~20° of the summer noon sun — nearly balanced.

All scenarios below are now computed on the POA basis for array 0. The same
treatment already applied to array 1's mounted scenarios, so the two arrays
are finally modeled consistently.

### Array 0 southern horizon (surveyed 2026-07-27)

POA modeling assumes an unobstructed southern horizon, so array 0's rooftop
sightline was surveyed with the same AR solstice-path method used at the
array 1 site. **Result: effectively clear.** The winter-solstice path runs
through open sky from roughly 09:00 to 15:45 EST; obstruction is limited to
tree foliage at the very start of the day (before ~08:45) and the treeline at
the very end (after ~15:45). The sun clears the distant hills and treeline
throughout the productive middle of the day.

Solstice geometry at this site: sun above horizon 08:33–16:42 EST, peak
elevation 18.6° at 12:37. Beam energy is heavily concentrated around solar
noon, so the surveyed clear window captures:

| Window | Share of solstice beam energy |
|---|---|
| 09:00–15:45 (as surveyed) | **96.3%** |
| 09:00–15:30 (conservative reading) | 94.3% |
| 08:45–16:00 (optimistic reading) | 98.2% |

Since beam is ~68% of December POA at 45° south, a 4–6% beam loss is a **2–4%
haircut on total winter POA** — within the noise of the coefficient bracket,
so the interim scenario numbers stand as computed.

For contrast, the 12:30–14:30 band that the building blocks at the array 1
site is alone worth **35.7%** of solstice beam energy. Array 0's rooftop
position is a fundamentally better winter site than array 1's ground
platform ever was, which is worth remembering when the array 1 remount is
planned — the cabin roof should be checked against this standard.

## Why the coefficient is not locked in

Nine days of post-fix telemetry produced 314 usable uncurtailed bins — more
than the 261 that built the original model — so **sample size is not the
constraint**. The constraint is that the estimate is not reproducible:

| Basis | June window (×8/7 for the fault) | July window (corrected) |
|---|---|---|
| POA, temperature-normalized | 1.60 (67% of nameplate) | 2.01 (84% of nameplate) |
| Clear-sky bins only, GHI | 1.33 | 1.86 |
| Sun ≤20° elevation, GHI | 1.18 (R² 0.00) | 0.90 (R² 0.40) |
| Sun >40° elevation, GHI | 1.38 | 1.91 |

Best-case fit quality is R² 0.56. The 84%-of-nameplate figure is above what
this class of hardware plausibly delivers, so the July end of the bracket is
likely biased high — probably by selection, since uncurtailed bins skew toward
cool-panel morning conditions.

**Confirmed by continued drift (2026-08-01 rerun).** Additional days moved the
fitted coefficient down and reduced fit quality rather than converging it:

| Window | Bins | Coefficient | % of nameplate |
|---|---|---|---|
| 2026-07-19 .. 07-27 | 308 | 2.01 | 84% |
| 2026-07-19 .. 07-30 | 373 | **1.90** | 79% |
| 2026-07-28 .. 07-30 (marginal) | 65 | 1.56 | 65% |
| 2026-07-19 .. 08-01 | 469 | **1.70** | 71% |

The marginal three July days alone read 1.56, close to the June figure of 1.60,
and the extended fit has now moved to 1.70 with R² only 0.44. This is what a
sample-composition-dependent estimator looks like from the inside: each window
is internally consistent and the windows disagree. Scenario tables therefore
quote the bracket. No running slope is treated as a midpoint or best estimate.

Root cause: the regression uses **modeled** irradiance as its independent
variable. Open-Meteo GHI is an hourly value on a ~1 km grid, polled every
30 minutes; it cannot track real cloud transients at a site with lake-effect
variability. That is irreducible error in *x*, which both caps the fit and
makes the slope depend on which conditions happen to land in the sample. More
summer telemetry will not fix it.

Ruled out as explanations: weather-feed gaps (48 samples/day at 30-minute
cadence, one 103-minute gap in nine days); charge-allocator throttling of the
Classic (0.9% of June bins near its limit, 0% in July); and array-geometry
misfit (POA reconstruction improves R² only from 0.44 to 0.56).

There is also no summer analogue for December's geometry. December sun is
≤18.7° elevation and due south, striking a 45° panel near-normal; summer's
low-elevation bins are morning and evening sun in the east and west, striking
the same panel at ~80° incidence. They share an elevation and nothing else.

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

With array 1 decommissioned, winter 2026–27 runs on array 0 alone. The pinned
ERA5 run and the 1.60–2.01 coefficient bracket give mean Dec 15–31 production
of **3.0–3.8 kWh/day**:

| Mode | Load | Worst 7-day, median winter | Median Oct–Apr net | Gross negative-day energy | Manual generator |
|---|---|---:|---:|---:|---:|
| Full occupancy | 5.15 + heater | −27.1 to −24.5 kWh | −18 to +281 kWh | 311–246 kWh | 47–37 starts, 306–238 kWh, 96–74 h |
| No refrigeration | 4.38 + heater | −21.7 to −19.1 kWh | +145 to +444 kWh | 230–179 kWh | 33–22 starts, 210–139 kWh, 66–44 h |
| Lean unattended caretaker | ~0.5 + heater | **+5.3 to +8.0 kWh** | +963 to +1262 kWh | 2–1 kWh | no generator; min SOC 75–77%, no empty hours |

Generator figures use 3.2 kW DC-bus-equivalent output, a planning start at 20%
SOC, and the operator's 90% stop target. They supersede the former
"bank-empty event" count, which instantaneously refilled an empty bank and was
not an operational generator policy. `Gross negative-day energy` is storage-
shifting pressure, **not** seasonal deficit or generator fuel energy; the
separate Oct–Apr net column makes that distinction explicit. Regenerate the
table with `python3 scripts/annual_model.py`.

Two conclusions changed with these corrections:

- **Unattended winter 2026–27 on array 0 alone has modeled margin, but the SOC
  floor is not an empirical guarantee.** The baseline hourly run bottoms at
  75% SOC. At the low coefficient, 2× heater duty bottoms at 35%, a 25% winter
  PV haircut bottoms at 69%, and the combined 2×-heater/25%-PV-loss case bottoms
  at 16%; none empties the bank in the nine ERA5 winters. Snow cover, heater
  duty and winter array response remain unmeasured, so this supports a
  conditional planning conclusion rather than a reliability claim.
- **Shedding refrigeration is no longer a strategy.** At a measured
  0.77 kWh/day reduces modeled generator use but does not eliminate it. Occupied
  winter still uses the generator under the stated SOC policy, so the
  operational lever is generator scheduling, not fridge discipline. The
  ~59 W of unidentified always-on overnight load is a larger target than the
  freezers.

### Post-mount scenarios (array 1 on the cabin roof, ~Sep 2027+)

`--array1` remains a **design-sensitivity mode, not a forecast**. It evaluates
both PR 0.55 and 0.70 rather than hiding their midpoint, prints a prominent
commissioning warning, and uses the same operational generator policy as the
Array 0 table. The old survey was taken below the planned rooftop height, the
performance ratio is assumed, and neither summer output nor winter shading is
known for the final installation. Publishing another precise outcome table
would imply evidence that does not exist.

The first defensible full-system run is deliberately gated on Array 1
commissioning in summer 2027: verify topology and controller telemetry, collect
uncurtailed output, replace the PR bracket, then carry the measured coefficient
through the following winter as observations accumulate. Until that gate,
`python3 scripts/annual_model.py --array1` is for comparing designs only.

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

## Battery capacity: 200 Ah vs 400 Ah

The hourly model corrects the old blanket statement that winter is purely
energy-limited. Storage cannot fix a sustained deficit, but it can capture
sunny-period surplus that the 8.7 kWh bank discards and carry it across a dark
spell. The installed 200 Ah bank is modeled at **8.704 kWh usable** (10.24 kWh
nominal × 85%); the exact doubled case is **17.408 kWh usable**. The audited
status quo and doubled-capacity results are below. Values are averages per
winter across the nine ERA5 winters; ranges span the Array 0 coefficient
bracket (`k=1.60–2.01`) and, where present, the provisional Array 1 performance
bracket (PR 0.55–0.70).

| Array configuration | Load scenario | Status quo: 8.704 kWh usable | Double: 17.408 kWh usable |
|---|---|---:|---:|
| Array 0 only | Full occupancy | 37–47 starts; 238–306 kWh / 74–96 h | **12–18 starts; 155–238 kWh / 49–74 h** |
| Array 0 only | No refrigeration | 22–33 starts; 139–210 kWh / 44–66 h | **7–12 starts; 90–156 kWh / 28–49 h** |
| Array 0 only | Lean unattended | no starts; minimum SOC 75–77% | **no starts; minimum SOC 87–88%** |
| Array 0 + Array 1 *(provisional)* | Full occupancy | 18–27 starts; 115–175 kWh / 36–55 h | **3–8 starts; 44–102 kWh / 14–32 h** |
| Array 0 + Array 1 *(provisional)* | No refrigeration | 10–16 starts; 67–101 kWh / 21–32 h | **1–4 starts; 15–47 kWh / 5–15 h** |
| Array 0 + Array 1 *(provisional)* | Lean unattended | no starts; minimum SOC 79–80% | **no starts; minimum SOC 89–90%** |

With Array 0 alone, doubling storage cuts starts by roughly two-thirds but does
not change the operational conclusion: occupied winter still requires routine
generator use. With Array 1, the doubled bank makes generator use occasional
rather than routine in the model, but does not eliminate it: full occupancy
still averages 3–8 starts per winter, and no-refrigeration 1–4.

Starts fall more than generator energy because the fixed 20%-to-90% SOC policy
puts roughly twice as much energy into each doubled-bank session. Added storage
has its greatest value when it can retain sunny-period PV that the status-quo
bank would discard; it cannot erase a sustained dark-week deficit. Array 1
figures remain design sensitivities until summer 2027 commissioning and
calibration. Reproduce other sizes with `--bank-usable-kwh`; none of these
figures is a battery-purchase recommendation.

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

1. **Battery heater duty vs temperature — hardware-deferred until summer
   2027 or later.** Until instrumentation changes are practical, carry the
   explicit 1×/2× stress cases rather than narrowing this assumption.
2. **A measured plane-of-array irradiance reference — hardware-deferred until
   summer 2027 or later.** Until then the Array 0 coefficient remains the
   1.60–2.01 bracket; more summer samples against modeled weather must not be
   presented as convergence. See
   [calibration limits](#why-the-coefficient-is-not-locked-in).
3. **Identify the ~59 W of unexplained always-on overnight load** (184 W
   measured overnight, ~93 W of identified always-on gear, ~32 W
   refrigeration). A second Sonoff S31 on a suspected circuit would settle
   it. This is nearly twice the refrigeration load and runs 24/7, making it
   the largest available load reduction.
4. **Seasonal refrigeration utilization:** repeat the combined-tier analysis
   in winter and after any thermostat-probe/thermal-mass change. This tests
   whether the July ~0.77 kWh/day and short refrigerator cycles carry into
   the conditions used by the annual model, then supports a corrected
   no-refrigeration scenario.
5. **AR solstice-path survey from the cabin rooftop mounting height**
   (whenever roof access exists, ideally near solstice-relevant sun angles):
   replaces the old +1.4/+2.1 m ladder follow-ups; quantifies how much of
   the December noon block the ~10 ft of extra height recovers.
6. **Commissioning gate, summer 2027:** post-mount recalibration of Array 1
   (`calibrate_pv.py <date>`): does per-kW effectiveness recover
   from the 16% flat floor toward the PR 0.55–0.70 band? Verify
   `epever.1 pv_voltage` ~108 V class (3s4p) on reconnect, and capture a
   clear-day trace when seasonally available. The first full empirical model
   run waits for this commissioning dataset; winter validation follows as the
   first mounted winter accumulates.

## History

- 2026-08-01 — model audit: historical weather pinned to ERA5; SOC simulation
  moved to hourly resolution; manual 3.2 kW generator modeled from configurable
  start SOC to the operator's 90% stop target; seasonal net, negative-day
  pressure and generator energy separated; stress scenarios and tests added.
  Array 1 results explicitly gated on summer 2027 commissioning
  ([journal](journal/2026-08-01.md)).
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
- 2026-07-27 — **model reformulated onto a plane-of-array basis.** Array 0's
  geometry (45° tilt, due south, 30 ft) was recorded for the first time;
  modeling it as GHI-proportional had understated its December output by ~2×.
  Array 0's coefficient replaced with a 1.60–2.01 W per W/m² POA bracket
  after nine days of post-wiring-fix telemetry failed to reproduce a stable
  point estimate. "No refrigeration" scenarios rerun at the metered
  4.38 kWh/day. Net effect: unattended winter on array 0 alone is comfortable
  rather than marginal, and fridge-shedding is no longer a useful winter
  lever ([journal](journal/2026-07-27.md)).
- 2026-07-27 — array 0's southern horizon surveyed (AR solstice path from the
  roof) and found effectively clear: ~96% of solstice beam energy falls in
  the unobstructed 09:00–15:45 window, so the POA reformulation's key
  assumption is confirmed and the interim winter numbers stand.
- 2026-07-30 — recalibrated on 12 days of post-fix telemetry: array 0 fits
  1.90 (down from 2.01 on 9 days; the marginal 3 days alone read 1.56),
  confirming the coefficient is sample-composition dependent rather than
  converging. `annual_model.py` rewritten onto the POA basis to match the
  documented method — it had still been GHI-proportional — and now fetches
  and caches its own irradiance data, brackets the coefficient, and models
  the post-remount system behind `--array1`
  ([journal](journal/2026-07-30.md)).
- 2026-07-20 — dedicated refrigeration trace measured ~0.77 kWh/day and
  27% capacity-normalized compressor duty; the earlier 2.6 kWh/day allocation
  and derived no-refrigeration scenarios were marked stale
  ([journal](journal/2026-07-20.md)).
