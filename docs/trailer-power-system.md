# Trailer Power System

A small, standalone 12 V solar system in the utility trailer, independent of
the cabin's 48 V system. It reuses the flooded lead-acid golf-cart batteries
retired from the house bank when the cabin moved to LiFePO4 in June 2026.

Nothing here is connected to the cabin system, and none of it is monitored by
the Raspberry Pi supervisor. It is a fully manual system: no telemetry, no
remote control, no logging. Operation is by the printed procedure in
[Runbook](#runbook) below.

Because the trailer is only powered when the site is occupied, the design
point is *intermittent light loads with long idle periods*, not continuous
service.

## Hardware

| Item | Value |
|---|---|
| Battery bank | 8x 6 V flooded lead-acid golf-cart batteries, retired from the cabin bank |
| Cell capacity | 230 Ah nominal each |
| Bank configuration | 2S4P — 12 V nominal, 920 Ah (see [Bank arithmetic](#bank-arithmetic)) |
| Charge controller | Victron Energy BlueSolar MPPT 100/30 |
| PV modules | 2x Enerwatt EWS-270M-60, wired in series (portable, stowed indoors when unoccupied) |
| Inverter | Aims, 1200 W continuous / 2400 W surge, 12 VDC to 120 VAC |
| Inverter feed protection | 300 A fuse on the positive battery feed to the inverter |
| Disconnects | System MASTER SWITCH; lever switch BATTERY TO CHARGE CONTROLLER; DC breaker PV TO CHARGE CONTROLLER |
| Controls location | Below and to the right as you face the battery bank |

Manuals live in Dropbox under `/manuals/solar/`:

- `Victron-BlueSolar-charge-controller-MPPT-100-30.pdf`
- `SolarPanels-3030110059EWS-270M-60spec.pdf`
- `victronconnect_manual.pdf` (VictronConnect app, for Bluetooth configuration)

These components are deliberately **not** in
[hardware/inventory.csv](../hardware/inventory.csv), which tracks the cabin
system only. Keep the two systems separate there to avoid confusing the
supervisor-facing inventory views.

### Bank arithmetic

The bank has eight 6 V, 230 Ah golf-cart batteries wired **two in series,
four strings in parallel (2S4P)** for 12 V nominal:

| Quantity | Value |
|---|---:|
| Nominal bank voltage | 12 V |
| Nominal bank capacity | 920 Ah (4 strings x 230 Ah) |
| Nominal energy | ~11.0 kWh |
| Usable energy at 50 % DoD | ~5.5 kWh |

Note that 50 % is the conventional flooded lead-acid depth-of-discharge
limit for cycle life. These are already-retired cells, so actual usable
capacity is materially below nameplate — treat 5.5 kWh as a ceiling, not an
expectation.

### PV array

Per-module specifications, from the Enerwatt spec sheet:

| Parameter | Per module | 2 in series |
|---|---:|---:|
| Maximum power Pmax | 270 W (±3 %) | 540 W |
| Voltage at max power Vmpp | 31.12 V | 62.24 V |
| Current at max power Impp | 8.71 A | 8.71 A |
| Open-circuit voltage Voc | 38.21 V | 76.42 V |
| Short-circuit current Isc | 9.25 A | 9.25 A |
| Cells | 60 | 120 |
| Module efficiency | 16.6 % | — |

Other module data: monocrystalline silicon; temperature coefficient of Voc
-0.31 %/°C, of Isc +0.033 %/°C, of Pmax -0.42 %/°C; NOCT 45 ±2 °C;
operating range -40 to +85 °C; maximum series fuse rating 15 A; maximum
system voltage 1000 VDC (IEC) / 600 VDC (UL); 1640 x 992 x 40 mm; 20 kg
each; MC4 connectors; 3 bypass diodes; 1000 mm leads.

At 20 kg and 1.63 m² each these are full-size modules, not lightweight
portables. Two-person handling is sensible for the deploy/stow cycle in the
runbook.

### Charge controller

Victron BlueSolar MPPT 100/30 specifications relevant here:

| Parameter | Value |
|---|---|
| Battery voltage | 12/24 V auto-select (one time only) |
| Rated charge current | 30 A |
| Nominal PV power at 12 V | 440 W |
| Maximum PV open-circuit voltage | 100 V |
| Maximum PV short-circuit current | 35 A |
| Maximum efficiency | 98 % |
| Self-consumption | 10 mA |
| Default absorption / float (12 V) | 14.4 V / 13.8 V, adjustable |
| Default equalization (12 V) | 16.2 V, **off by default** |
| Temperature compensation | -16 mV/°C at 12 V (internal sensor) |
| Operating temperature | -30 to +60 °C (full output to 40 °C) |
| Enclosure | IP43 electronics / IP22 connection area, indoor type 1 |
| Data port | VE.Direct; Bluetooth Smart built in |

## Design Checks

### Cold-weather PV voltage — this is why the panels are in series, and why there can only be two

The 100 V absolute PV input limit is the binding constraint on array
configuration. Open-circuit voltage rises as temperature falls, and the site
is near Wawa, Ontario (see [site.md](site.md)).

String Voc = 76.42 V x (1 + 0.0031 x (25 - T)):

| Cell temperature | String Voc | Margin to 100 V |
|---:|---:|---:|
| +25 °C (STC) | 76.4 V | 24 % |
| -20 °C | 87.1 V | 13 % |
| -30 °C | 89.4 V | 11 % |
| -40 °C | 91.8 V | 8 % |

Two modules in series stay under the limit across the full ambient range the
site sees, with roughly 8 % headroom at the panel's own -40 °C rating floor.
That margin is real but not generous: the worst case is a very cold, bright,
clear morning with the array open-circuit — precisely the condition created
by connecting the panels before closing the PV breaker, which is why the
runbook sequences the breaker last.

**Three modules in series would be 114.6 V at STC and would destroy the
controller.** Do not add a third panel to this string. If array capacity is
ever expanded, add a second parallel string of two, and re-check string
fusing at that point (three or more parallel strings would require it; two
does not, since Isc back-feed from a single sibling string stays well under
the 15 A module fuse rating).

Series cell count is 120, within the manual's 144-cell maximum for a 12 V
battery, though above the 72 cells it names as optimal for controller
efficiency. Vmpp of 62 V is far above the Vbat + 5 V startup threshold, so
the array starts charging in weak light.

### The array is over-panelled relative to the controller

540 W STC against a 440 W nominal rating at 12 V — about 23 % over. This is
safe and intentional-looking: Victron controllers clip to their rated output
current rather than faulting, and the Voc and Isc limits (76 V of 100 V,
9.25 A of 35 A) are both respected. Harvest is capped at 30 A, roughly 430 W
at absorption voltage.

At this latitude, with the modules hand-leaned against a wall rather than
optimally mounted, the array will rarely reach STC output anyway. The
over-panelling buys better harvest in weak light and shoulder seasons at the
cost of clipping a few hours around midday in summer. That is the right
trade for this system.

### Charge rate is very low relative to the bank

30 A into 920 Ah is **C/31**, about 0.033C. Flooded deep-cycle batteries
generally want C/10 to C/13 for an efficient bulk charge.

The consequences are worth being explicit about, because they shape how the
system should be used:

- A recharge from 50 % DoD is ~460 Ah. At the 30 A ceiling that is 15+ hours
  of *full-current* sun — realistically several days of good weather, more
  in shoulder seasons.
- Chronic undercharging is the standard failure mode for flooded lead-acid.
  Cells that never reach a full absorption termination sulfate and lose
  capacity permanently. On a bank this large relative to the array, that
  risk is structural, not hypothetical.
- The system therefore suits light, intermittent loads — tools, lighting,
  charging — with the array left connected for as many daylight hours as
  possible during occupancy. It does not suit sustained heavy draw.

The large bank is not a mistake: it is free retired capacity, and the surplus
Ah means shallow cycling, which is the kindest possible duty for used
lead-acid. But the array cannot restore a deep discharge in any useful
timeframe. Size expectations accordingly, and consider whether an occasional
mains or generator charge is worth arranging to give the bank a genuine full
absorption cycle.

### Inverter loading

At 1200 W continuous output and ~85 % efficiency, DC draw is roughly
**115–125 A** at 12 V. The 2400 W surge implies **230–250 A** transient.

| Draw | Nominal runtime to 50 % DoD |
|---|---:|
| 1200 W (full continuous) | ~3.5 h |
| 500 W | ~9 h |
| 150 W | ~30 h |

Runtimes above already include a rough Peukert derating; at 1200 W the bank
is discharging at about C/8, where flooded lead-acid delivers materially
less than its C/20 nameplate. Treat these as optimistic for retired cells.

Sustained operation near 1200 W is hard on both the cells and the cabling.
Occasional surge use is what the inverter is sized for.

## Open Items

Ordered roughly by how much they matter.

1. **Inverter feed fuse — sizing confirmed, two details left.** A 300 A fuse
   is installed on the positive battery feed to the inverter. The rating is
   sensibly chosen: it clears the ~115–125 A continuous and ~230–250 A surge
   draw without nuisance-blowing, while still being far below what the bank
   can deliver into a fault. Two things remain to check, because a fuse
   protects the *cable*, not the inverter:
   - **Cable ampacity.** A 300 A fuse only protects conductors rated at or
     above roughly 300 A — in practice 2/0 AWG or larger, depending on
     insulation temperature rating and run length. If the inverter cable is
     smaller than that, the cable can overheat at currents the fuse will
     happily pass indefinitely. Measure or read the cable gauge and record
     it here.
   - **Fuse class and DC interrupt rating.** Confirm what type it is. A
     920 Ah flooded bank can push several thousand amps into a bolted fault,
     which is above the DC interrupt rating of common ANL-style fuses.
     Class T (~20 kA at 125 VDC) or MRBF (~10 kA) are the appropriate
     choices at this bank size. Also confirm the fuse sits close to the
     battery positive terminal — the unprotected stub between terminal and
     fuse should be as short as practical.
2. **Charging ventilation.** The runbook charges the bank with the trailer
   door closed and locked. Flooded lead-acid vents hydrogen during
   absorption and equalization, and a trailer is a small volume. Confirm
   what passive ventilation exists at the high point of the space. This
   is likely fine at C/31 charge rates, but it should be a checked fact
   rather than an assumption — especially if equalization is ever enabled.
3. **Rotary switch position.** Determine the controller's preset. Positions
   1 and 2 (14.3 V / 14.4 V absorption, 13.8 V float at 12 V) cover flooded
   deep-cycle types; position 2 is the factory default. Confirm the setting
   matches the batteries and record it. Note that any change made over
   Bluetooth or VE.Direct overrides the rotary switch, and turning the
   switch overrides prior app settings.
4. **Equalization policy.** Automatic equalization is off by default.
   Periodic equalization is normally beneficial for flooded cells and would
   pair naturally with the seasonal electrolyte top-up already in the
   runbook — but it increases gassing and water loss, so settle item 2
   first. Decide, then record the decision and interval here.
5. **12 V/24 V auto-detect.** The controller self-selects battery voltage
   **once**. If this unit ever ran on a 24 V bank it is still set to 24 V
   and must be changed manually via VictronConnect. Verify it reads 12 V.
6. **Status LED meanings.** The runbook describes a blue light flashing when
   the battery is connected and going solid once PV is connected. The manual
   documents three LEDs — bulk, absorption, float — plus fault codes and a
   rotary-position blink code, and notes the bulk LED blinks briefly every
   3 s when powered with insufficient power to charge. Identify which
   physical LED the runbook refers to and reconcile the description.
7. **Switch and breaker ratings.** Record make, model, and DC ratings for
   the MASTER SWITCH, the BATTERY TO CHARGE CONTROLLER lever switch, and
   the PV TO CHARGE CONTROLLER breaker. DC load-break capability matters
   for all three.
8. **Inverter model.** The specific Aims model is unrecorded. Determine
   whether it is pure sine or modified sine — it decides what loads are
   safe on it — and note its low-voltage cutoff, which is the bank's only
   automatic protection against over-discharge.
9. **Battery condition.** Load-test or at minimum record resting voltages per
   string; retired cells are rarely matched.
10. **Controller placement.** The manual requires the controller be mounted
    vertically with terminals down, close to the battery but never directly
    above it, on a non-flammable substrate, with charger and battery ambient
    temperatures within 5 °C of each other. Confirm the install satisfies
    this, particularly the "not above the battery" rule.
11. **Optional telemetry.** The controller has Bluetooth built in.
    VictronConnect on a phone gives harvest history and state of charge with
    no wiring. Worth doing on a visit; not worth building anything for.

## Runbook

The operator-facing procedure, as posted in the trailer
([print-ready PDF](../output/pdf/trailer-power-runbook.pdf)).

This section is the single source for that card. `scripts/generate-trailer-runbook.py`
parses the subsections below and renders the one-page PDF, so editing the
procedure here and regenerating keeps the posted copy in step:

```sh
.venv/bin/python scripts/generate-trailer-runbook.py
```

The "Why the order matters" subsection is background for this document and is
deliberately left off the printed card.

### General information

- The system should be powered down completely and the solar panels stowed
  inside the trailer when the site is unoccupied.
- All controls are below and to the right as you face the battery bank.
- Battery electrolyte levels should be topped up once per season.
- **CAUTION:** Never turn on PV unless MASTER and BATTERY TO CHARGE CONTROLLER
  switches are ON, it will damage the charge controller.

### Using the inverter

1. Ensure the system MASTER SWITCH is in the ON position.
2. Press and hold the black POWER button on the top panel of the inverter
   until the green status LED lights. You should be able to hear the fan at
   this point.
3. Attach your 120 VAC load to the plug exiting the bottom of the inverter.
4. When done using the inverter, press and hold the black POWER button until
   the green status LED goes off. Verify that the inverter fan is no longer
   running.

### Charging the batteries

1. Ensure that the DC breaker labelled PV TO CHARGE CONTROLLER is in the OFF
   position.
2. Remove the two solar panels from the trailer and lean them against the
   south wall next to the doorway.
3. Connect the solar panels in series (positive lead to negative lead) then
   attach the extension cables running to the charge controller.
4. Ensure the system MASTER SWITCH is in the ON position.
5. Move the lever switch labelled BATTERY TO CHARGE CONTROLLER to the ON
   position. A blue status light on the charge controller should be
   flashing.
6. Move the DC breaker labelled PV TO CHARGE CONTROLLER to the ON position.
   The blue status light on the charge controller should now be solid.
7. Close and lock the trailer door.

### Shutting down the system

1. Ensure the DC breaker labelled PV TO CHARGE CONTROLLER is in the OFF
   position.
2. Ensure the lever switch labelled BATTERY TO CHARGE CONTROLLER is in the
   OFF position. Verify that no status light on the charge controller is
   lit.
3. Ensure the system MASTER SWITCH is in the OFF position.
4. Disconnect all the connectors on the solar panels and bring them and the
   extension cables inside the trailer.
5. Close and lock the trailer door.

### Why the order matters

The charging and shutdown sequences are not arbitrary, and the manual backs
them up. Worth knowing so the steps survive being paraphrased by whoever
uses the trailer next:

- **Battery before PV, always** (charging steps 5 then 6). The Victron
  manual states the connection sequence explicitly: connect the battery
  first, then the solar array. The controller needs the battery present to
  detect system voltage and to have somewhere to put PV energy.
- **PV breaker off while handling panels** (charging step 1, shutdown step
  1). Opening the PV breaker first means the panel leads are being made up
  and broken into an open circuit rather than under load. MC4 connectors are
  not load-break rated, and a 12 V system's DC arc at 76 V is perfectly
  capable of pitting a contact or starting a fire.
- **PV breaker off before the battery switch on shutdown** (shutdown steps 1
  then 2). Same reason in reverse: never leave the array feeding a
  controller whose battery has been disconnected.
- **Panels stowed indoors when unoccupied.** Beyond theft and weather, this
  guarantees the array cannot be energised while nobody is present.

## See Also

- [site.md](site.md) — location, climate, and solar geometry for the site.
- [safety.md](safety.md) — electrical and operational safety notes for the
  cabin system; the general DC-work practices apply here too.
- [subsystems/battery-bank.md](subsystems/battery-bank.md) — the LiFePO4
  bank that replaced these batteries in the cabin.
- [runbooks/lead-acid-to-lifepo4-changeover.md](runbooks/lead-acid-to-lifepo4-changeover.md)
  — the cabin changeover that retired this hardware.
