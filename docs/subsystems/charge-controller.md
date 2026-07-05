# Charge Controller

## Hardware

The system runs two PV sources on two charge controllers (MidNite Classic on array 0, EPEver TEP10425 on array 1). Code and documentation refer to charge-controller/PV-source telemetry generically rather than assuming the MidNite Classic is the only solar input.

| Item | Value |
|---|---|
| Manufacturer | Midnite Solar |
| Model | Classic 200 |
| Role | Solar charge controller |
| Battery system | 48 V nominal |
| PV input | TBD |
| Firmware | TBD |
| Communication interface | TBD |
| Optional battery-current accessory | MidNite WhizBang Jr, likely avoid/defer to preserve AUX2 |

## PV Sources

| PV source | Controller | Array | Status | Notes |
|---|---|---|---|---|
| PV array 0 | MidNite Solar Classic 200 | Canadian Solar CS6X-300-adjacent modules, 4s2p | In service | 8 modules total; exact module ratings may vary around 295-305 W |
| PV array 1 | EPEver TEP10425 | Canadian Solar CS6X-300-adjacent modules, 3s4p | In service (PV connected 2026-06-16; rewired 3s4p 2026-07-02); platform mount construction outstanding | 12 modules total; exact module ratings may vary around 295-305 W. See [Array 1 String Topology](#array-1-string-topology-3s4p-decided-2026-07-02) |

## Array 1 Controller (EPEver TEP10425)

**Decided: the EPEver TEP10425 is the array 1 controller**, installed and in
service (RS485 Modbus RTU on `/dev/epever-rs485`, read and write; PV connected
2026-06-16). It won on PV input headroom — 250 V max Voc at lowest temperature,
versus the once-contended Victron BlueSolar MPPT 150/85's 150 V absolute limit,
which ruled out 4s strings of the CS6X-class modules outright. The Victron is no
longer a candidate: that hardware has been lost, and the Victron-bus experiment
is closed.

EPEver manual notes (saved at `~/Dropbox/manuals/solar/TEP-Manual-EN-V1.1.pdf`):
two PV inputs, IP20, common-negative grounding, local parameter setting,
RS485 Modbus, built-in BMS communication port, and built-in CAN parallel
communication port. The manual also describes a native closed-loop BMS mode
(BPRO/UBS) that follows BMS-published charge voltage/current limits directly.
**We do not use it as the charge authority:** the supervisor's closed-loop
charge allocator is the single policy authority across both controllers (see
[Real-Time Charge Current Allocation](../charge-current-allocation.md) and the
[epever-tep10425 research note](../research/epever-tep10425.md) for why the
native mode is not trusted with this bank). The allocator writes the EPEver's
current register and charge coil; the EPEver still owns its own CV voltage
regulation.

### Array 1 String Topology (3s4p, decided 2026-07-02)

Array 1 is wired **3s4p — four parallel strings of three panels in series,
one string per physical row** of the 3-wide x 4-high landscape layout, all
four strings on a single EPEver PV input.

Why 3s rows instead of the originally planned 4s3p: the platform site's
winter sun is partially occluded by the building roofline to the SSW (AR
solstice-path survey from the first-tier panel position, 2026-07-02 — the
midday 12:30-14:30 band grazes the barrel roof; trees own the morning). The
occlusion edge is horizontal and sweeps the array bottom-up, so string
topology determines how output degrades:

- **3s rows (chosen):** each row is one parallel string. N shaded rows cost
  exactly N/4 of output; lit rows keep operating at their MPP. With three
  rows dark the top row still delivers (~108 V Vmp, well inside the MPPT
  window above a ~53 V bus).
- 4s columns (rejected): every string spans all four rows; three shaded rows
  leave every string at ~36 V — below the battery — and the array delivers
  nothing despite a fully lit top row.

Electrical envelope on one TEP10425 input: 3s Vmp ~108 V; cold Voc
(~-40 °C) ~165 V against the 250 V limit; 4p ~33 A Imp / ~35.5 A Isc
against the 50 A per-input limit; ~68 A battery-side at full output against
the 100 A rating.

Known trade-offs, accepted:

- **Feeder margin is thin on paper.** The buried home run is one pair of
  8 AWG, 70 ft. Code design current for the feeder is Isc x 1.56 ~ 55 A,
  at/above the 8 AWG rating (50 A at 75 °C terminations, ~55 A for 90 °C
  wire — confirm the buried insulation class). Physically self-limiting
  (real max ~35 A, buried copper, 50 A upstream disconnect); the charge
  allocator can also pin the EPEver battery-side limit to ~55-60 A to cap
  feeder current near 30 A. 4s3p (39 A design) is the fallback if the
  installation ever needs a clean code pass.
- **Voltage drop** ~2.9 V at full 33 A output (~2.7%, ~95 W peak) vs ~1.5%
  for 4s3p. The extra loss lands in bright summer hours, which are
  curtailment-bound anyway; winter currents make it negligible.
- Four parallel strings on one input make **per-string 15 A fuses
  mandatory** (fault back-feed from three siblings ~27 A exceeds the
  panels' 15 A max-series-fuse rating).
- **PV2 stays free**, reserved for the provisional third array at a
  different site (its own orientation, its own MPPT channel via the INDE
  connection mode — holding `0x9042`). A 2+2 INDE split of array 1 across
  both inputs was considered and rejected to keep PV2 free and avoid
  re-trenching the single buried feeder.

### Consolidation option (considered 2026-06-10, parked)

The TEP10425's two PV inputs could take both arrays, retiring the Classic.
Decision: **keep the Classic on array 0.** Rationale: controller redundancy
(either unit failing still leaves solar charging — the consolidated
single-point failure is the worst available off-grid), ~6 kW combined
nameplate would clip the EPEver's 5,200 W ceiling on the best days, the
multi-controller supervisor cost is already paid (actor threads;
charger-agnostic charge allocator), and a subsystem with nine years of trouble-free
service should not be disturbed to win marginal simplicity. The dual input
is instead the **Classic-failure contingency**: if the Classic dies, both
arrays move to the EPEver with the control path already proven.

Charge-policy coherence across two controllers: chargers on a shared bus
do not negotiate — each regulates to its own setpoints and the highest
setpoint wins the top of charge. Both controllers must therefore carry the
same conservative profile, with the supervisor as the single policy
authority (see the epever research note for why the EPEver's native BMS
mode is not that authority with this bank).

## Existing Disconnects

| Location | Installed device | Current rating | Poles | Notes |
|---|---|---:|---:|---|
| PV array 0 to Midnite Solar Classic 200 PV input | Array-side disconnect / breaker | 20 A | 2 | Existing installation. This is likely undersized if array 0 is reconfigured from 4s2p to 2s4p; confirm DC/PV voltage rating, load-break rating, and conductor ampacity before reuse. |
| Midnite Solar Classic 200 battery output to 48 V battery bus | Battery-side disconnect / breaker | 100 A | 2 | Existing installation. This is a reasonable order of magnitude for the Classic output if the breaker, enclosure, wiring, and installation are DC-rated and correctly marked. |

## Telemetry Goals

| Measurement | Source | Priority | Notes |
|---|---|---|---|
| PV voltage | Classic 200 | High | Useful for array state and troubleshooting. **Note: Classic reports ~21 V at night** (phantom voltage from battery bleeding through FET body diodes into the PV measurement circuit — a known Classic hardware characteristic). Do not use Classic PV voltage to infer day/night state; use PV current or power instead. EPEver correctly reports 0 V at night. |
| PV current | Classic 200 | High |  |
| Charge output current | Classic 200 | High | Current into battery bus |
| Charge stage/state | Classic 200 | High | Bulk, absorb, float, resting, fault, etc. |
| Classic-local net battery current | WhizBang Jr, if installed | Low | Useful for end-amps logic and cross-checks, but consumes AUX2 and is not required for basic charge-stage visibility |
| Daily energy harvest | Classic 200 | Medium | Useful for system performance history |
| Controller temperature | Classic 200 | Medium | Watch thermal behavior |
| Faults/alarms | Classic 200 | High | Needs exact message mapping |

## Charge-Stage Vocabulary

With two controllers feeding one staged-charging goal, their differing
stage words are normalized to a single canonical vocabulary (the Classic's,
which tracks industry-standard terms) in
[`charge_stage.py`](../../software/pi-controller/src/offgrid_power/charge_stage.py).
Internal logic (e.g. the charge allocator) and all displays consume the
canonical stage; the controller's native word is shown in parentheses where
it differs.

The normalization lives below the API: each controller's API data block
carries the stage as a `{canonical, vendor}` pair (`NormalizedStage`), where
`vendor` is set only when the native word adds information beyond the
canonical name. Renderers stay vendor-agnostic — they display the canonical
name and append `vendor` in parens only when present — so no renderer needs
to know any controller's stage dialect.

| Canonical | Classic native | EPEver native |
|---|---|---|
| Bulk | BulkMppt | (folded into Boost) |
| Absorb | Absorb | Boost |
| Float | Float, FloatMppt | Float |
| Equalize | Equalize | Equalize |
| Resting | Resting | No charging |
| HyperVoc | HyperVoc | *(not supported)* |

The EPEver has no distinct bulk stage — it reports `Boost` for both the
constant-current climb and the constant-voltage hold — so `Boost` maps to
the canonical `Absorb`. An EPEver showing `Absorb` may therefore still be
physically in the bulk phase.

Not every canonical stage is supported by every vendor, which is expected:
each controller's map only emits the values it can reach. `HyperVoc`
(the Classic's PV-overvoltage self-protection) is Classic-only and is kept
distinct from `Resting` so the protection state stays observable rather than
hidden behind ordinary "not charging".

## Local Modbus Probe

The Classic is reachable on the LAN over Modbus TCP. Use the read-only probe script for quick local checks:

```sh
source .venv/bin/activate
python scripts/classic-probe.py --host 192.168.0.10 --raw
```

`classic-probe.py` reads live telemetry and selected charge configuration registers. Keep it read-only.

For charge-setting writes, use `scripts/classic-charge-settings.py` and the policy in [Charge Management](../charge-management.md). The guarded writer performs the Classic Ethernet Modbus unlock, checks planned settings against BMS CVL/CCL, and can either make a live-only change with `--no-persist` or persist the setting to EEPROM by default.

## Changeover Runbook

Use [Lead-Acid To LiFePO4 Changeover](../runbooks/lead-acid-to-lifepo4-changeover.md) when replacing the current legacy lead-acid bank with the Eco-Worthy Cubix 100 LiFePO4 battery bank. Keep the procedure there because it is an action-oriented runbook with rollback steps, not just subsystem background.

## Control Boundaries

The charge controller owns charge regulation. The Raspberry Pi may monitor it and may later adjust non-critical settings only if the interface is reliable and the change is reversible from the controller front panel.

Because this is legacy equipment, assume its built-in charge stages may be lead-acid oriented until proven otherwise. The supervisor must specifically account for LiFePO4 behavior: bulk/absorb may be useful, sustained float should not become the normal long-term state after the bank is full, and equalization must be disabled for normal operation.

For the difference between the Classic's Float stage and actual PV power output, see [FAQ](../faq.md).

The supervisor should surface Classic `HyperVoc` explicitly. In this state the Classic is protecting itself from PV input above its normal operating range and may not charge until array voltage falls back inside range. This can be expected during very cold bright winter conditions if the array is intentionally sized near the Classic 200's upper input envelope.

The Eco-Worthy ESM-100/BMS should be the first battery SOC/current source for the Pi. The Classic already reports its own charge stage, such as bulk, absorb, float, resting, or fault, without a WhizBang Jr.

See [Charge Management](../charge-management.md) for the policy that compares Classic charge settings against BMS-advertised CVL/CCL and raises read-only alerts for cell-voltage and cell-delta conditions.

AUX2 was previously connected to WhizBang Jr; that wiring has been removed. The Eco-Worthy BMS/ESM-100 over CAN is the primary battery current and SOC source, making WhizBang Jr redundant. AUX1 is free; AUX2 has been repurposed as the hardware charge-disable input.

## AUX2 — Hardware Charge Disable

AUX2 is the hardware charge-disable line. The supervisor drives relay CH2 (GPIO 27) to apply a high voltage signal (>6 V) to the AUX2+ terminal whenever it commands 0 A to the Classic, forcing the Classic to Resting independent of Modbus write success.

AUX2 has two terminals: AUX2+ (signal) and AUX2− (GND reference). It is a voltage input when configured as an input function. Maximum input voltage is 15 V; minimum is 0 V.

Relay CH2 is wired: COM → 12 V supply (shares GND with Classic); NO → Classic AUX2+ terminal; Classic AUX2− → GND. When the relay closes, 12 V (>6 V threshold) is applied to AUX2+ and the Classic enters Resting.

AUX2 is reconfigured to "Active HIGH (input) turn off" (register 4165, AUX2 function value 15 — >6 V on AUX2+ forces Classic to Resting). Register 4165 is now **0x4F01** (AUX2 function = 15, Auto; AUX1 unchanged), written and EEPROM-persisted via `scripts/classic-aux2-config.py`.

Charge disablement via the allocator has been confirmed working end-to-end: the allocator commanding 0 A / disable to the Classic closes relay CH2, AUX2 sees >6 V, and the Classic enters Resting.

### BMS charge-enable fault tolerance (missing 0x35C)

The allocator's charge-disable is gated on the BMS charge-enable request (Pylon
`0x35C`, `request_flags.charge_enable`). That frame is abundant on the bus
(~72 Hz), but the 1.5 s battery read occasionally assembles a snapshot without it
(~1 read in 2000 — a benign gs_usb/USB delivery hiccup, **not** CAN bus
ill-health: bus error counters stay clean). Previously a missing frame
(`request_flags is None`) was read as "BMS said stop", pulsing charge off for one
cycle and briefly energizing relay CH2 (observed 2026-07-05 06:25 at 74% SOC).

`ChargeEnableResolver` (`charge_ceiling.py`) now separates the two conditions:

- **Frame present** → use the bit verbatim; a genuine BMS stop acts immediately.
- **Frame absent, within grace (`CHARGE_ALLOC_ENABLE_HOLD_S`, default 45 s)** →
  hold the last-known value (debounce a dropped frame).
- **Frame absent beyond grace (sustained blindness)** → **release** to the
  controllers' own regulation rather than latching charge off. A sustained stop is
  the *more dangerous* failure for an off-grid pack (it walks the bank to blackout
  and takes the supervisor + watchdog down), and releasing is safe because both
  controllers self-regulate to a conservative absorb/float (57.0 V, ~1.4 V below
  the BMS CVL of 58.4 V) and the BMS keeps independent hardware over-voltage and
  under-temperature cutoffs. This also matches the relay's own fail-off wiring: a
  fully-dead Pi already de-energizes CH2 → charge enabled. Note total CAN loss
  (`battery is None`) already released via the ceiling's "no battery telemetry"
  path; this fix removes the inconsistency where *partial* loss was treated more
  conservatively than *total* loss.
- **Cold gate:** a blind release is suppressed (hold off) when the CAN-independent
  ambient sensor (GPIO 4) reads at/below `CHARGE_ALLOC_COLD_RELEASE_BLOCK_C`
  (default 2.0 °C), since with the BMS dark the supervisor can't see pack
  temperature; the BMS hardware under-temp cutoff remains the ultimate backstop.

Entering/leaving the degraded (blind) state emits a `charge_enable_degraded`
telemetry event and a journald line, so a real request-flags outage is loud.

Known AUX functions relevant to this project (from register map Table 4165-4):

| AUX port | Function | Value | Behavior | Status |
|---|---|---:|---|---|
| AUX2 | WhizBang Jr | 18 | Aux 2 commands and receives WB Jr data | Removed |
| AUX2 | Active HIGH (input) turn off | 15 | >6 V on AUX2+ forces Classic to Resting (0–15 V input) | **Active** — register 4165 = 0x4F01, relay CH2 wired and confirmed working |
| AUX2 | Active HIGH (input) Float | 17 | >6 V on AUX2+ forces Classic to Float | Available for future use |
| AUX1 | — | — | Output only (relay or 0–14 V signal); not used for charge disable | Free |

Possible future supervisory actions:

- Alert when the controller reports a fault.
- Alert when expected solar production is absent.
- Record charge-stage history.
- Compare charge-controller battery voltage with battery-bank telemetry.
- Detect excessive time in absorb or float.
- Alert immediately if the controller enters equalize.
- Keep equalize disabled or locked behind a deliberate manual procedure.
- Move the Classic to a resting/off/reduced-charge state after a full-charge condition via Modbus write.
- Re-enable solar charging when bank voltage or SOC falls below a documented restart threshold.
- On BMS charge-disallow or approaching overvoltage/low-temperature limit, command the Classic to stop or reduce charge before the battery BMS opens.
- If a hardware fallback is needed, interrupt PV/source input to the Classic before interrupting the Classic-to-battery connection.

## Wiring And Communications

Document:

- PV array wiring into the controller.
- Controller battery output wiring to the 48 V bus.
- DC breakers/disconnects on PV and battery sides.
- Network or serial connection used for telemetry.
- IP address, bus address, or other device identifier.

## Open Questions

- How is equalization disabled in the Classic configuration, and can the supervisor verify that state?
- Can Classic Mode Off or current-limit control be issued quickly and reliably enough to prevent BMS charge disconnect?
- Is a DC-rated PV input contactor/disconnect needed as a hardware fallback for charge inhibit?
