# Battery Bank

## Hardware

| Item | Value |
|---|---|
| Batteries | 2x Eco-Worthy Cubix 100 rack-mount batteries |
| Nominal voltage | 48 V |
| Capacity | 100 Ah each, 200 Ah total nominal |
| Chemistry | LiFePO4 |
| BMS | Built in, 100 A BMS per battery |
| Remote monitor | Eco-Worthy ESM-100 |
| Physical arrangement | Rack-mounted, details TBD |
| Parallel wiring | Diagonal / cross-connected main takeoff, corrected 2026-06-03 |
| Candidate Pi interface | DSD TECH SH-C31G isolated USB-to-CAN adapter |

## Telemetry Goals

| Measurement | Source | Priority | Notes |
|---|---|---|---|
| Bank voltage | BMS / ESM-100, inverter, or charge controller | High | Prefer BMS/ESM-100 as first canonical value, with per-device comparison |
| Bank current | BMS / ESM-100 | High | Positive for charging, negative for discharging |
| State of charge | BMS / ESM-100 | High | ESM-100 reports SOC; confirm whether Pi can read the same value over CAN/RS485 |
| Battery temperature | BMS or external sensors | High | Track per-battery if possible |
| Per-battery voltage | BMS | Medium | Useful for imbalance detection |
| BMS alarms/faults | BMS | High | Needs exact interface research |
| Charge/discharge limits | BMS/manual specs | Medium | Important for control policy and alarms |

## Candidate Communication Path

Eco-Worthy documentation says the Cubix 100 supports Bluetooth/Wi-Fi app monitoring, RS485, CAN, and RS232. The current candidate for Pi-side CAN access is the DSD TECH SH-C31G isolated USB-to-CAN adapter.

This is a reasonable adapter choice for CAN experimentation because it is USB-attached, isolated, cabled with USB Type-B instead of a tight USB dongle, based on CANable 2.0, supports CAN 2.0A/B up to 1 Mbps, and is described by DSD TECH as SocketCAN-compatible.

The battery protocol table shows the CAN port as a host-computer/inverter port. It gives the CAN RJ45 pinout as pin 4 = CANH1, pin 5 = CANL1, pins 3 and 6 = GND, with pins 1, 2, 7, and 8 not connected. The default listed CAN protocol is Pylon CAN bus protocol V2.0.6_220510.

Validation needed before treating CAN as the production battery telemetry path:

- Confirm the physical CAN RJ45 pinout against the actual battery/manual revision.
- Confirm CAN termination and baud rate.
- Confirm whether Eco-Worthy publishes useful BMS telemetry over CAN, not only inverter-facing summary messages.
- Confirm the protocol selected in the Eco-Worthy app/tool, starting with the default Pylon CAN protocol.
- Compare CAN data against the Eco-Worthy app and/or RS485 host software.

The Pi software defaults to `BATTERY_CAN_PROTOCOL=pylon`. A later change to the Eco-Worthy app's "Victron" protocol can use `BATTERY_CAN_PROTOCOL=ecoworthy-victron` or `offgrid-supervisor --battery-can-protocol ecoworthy-victron`. The May 31, 2026 battery-only capture of that app setting still used 500 kbit/s standard CAN frames and preserved the core display metrics: SOC/SOH, pack voltage/current/temperature, charge limits, cell voltage range, cell temperature range, and installed capacity. It did not look like VE.Can/NMEA2000 at 250 kbit/s.

Keep RS485 and RS232 as fallback or parallel research paths because the protocol table also shows host-computer-capable RS485 and RS232 options. Preferred RS485 fallback hardware is an isolated USB-to-RS485 adapter, such as the Waveshare USB TO RS485/422 industrial isolated converter.

See [Charge Management](../charge-management.md) for how BMS-advertised CVL/CCL, cell voltage, and cell delta are used by the supervisor.

## Shunt Decision

The Eco-Worthy ESM-100 remote monitor changes the battery monitor decision. It already displays battery voltage, current, and state of charge, so a separate WhizBang Jr shunt is not required for the first battery telemetry build if the Pi can obtain equivalent BMS data over CAN or RS485.

Keep the WhizBang Jr / external shunt as optional, and likely avoid it if AUX2 becomes important for Classic control:

- Independent cross-check against BMS-reported current and SOC.
- Midnite Classic integration if WhizBang-derived net battery current is useful enough for end-amps absorb termination logic to justify consuming AUX2.
- Possible canonical system-level current source if BMS telemetry proves incomplete or inaccessible.

Reasons to defer the shunt:

- Adds another high-current measurement point and wiring change.
- Duplicates values already visible on the ESM-100 for Pi-level battery monitoring.
- Consumes Classic AUX2, which may be more useful for charge inhibit or other high-level Classic control.
- Does not remove the need to read BMS alarms, charge-allowed state, low-temperature inhibit, and per-battery details.

## Control Boundaries

The Raspberry Pi should not be the primary battery safety device. Battery protection remains the responsibility of the BMS, fuses, breakers, disconnects, and any required external protection hardware.

The bank is LiFePO4, so supervisory charge policy should avoid sustained lead-acid-style float. The battery BMS remains the final protection layer, but normal operation should not depend on the BMS repeatedly blocking charge because legacy chargers are holding the bank too high for too long.

Avoid designs that depend on the internal battery BMS opening while chargers are active. If a BMS opens under charge, the remaining DC bus can see a sharp transient because the charger has suddenly lost the battery as its voltage reference and energy sink. The supervisor should use charge-allowed telemetry and conservative thresholds to stop chargers before the BMS must protect the cells.

Possible future supervisory actions:

- Alert on low SOC, high/low voltage, high temperature, or BMS fault.
- Request load shedding through documented auxiliary contactors.
- Inhibit non-critical loads when battery reserve is low.
- Publish charge-allowed, discharge-allowed, full-charge, and recharge-needed states for charger supervisors.
- Alert if bank voltage remains near the top of charge for longer than the configured LiFePO4 hold-time limit.
- Alert if either battery reports charge disconnect, overvoltage, low-temperature charge inhibit, or disappears from telemetry during active charging.

Do not implement any battery-related control until the disconnects, manual override path, and fail-safe state are documented.

## Wiring And Protection

The two Cubix 100 batteries are wired as a parallel bank with a diagonal/cross-connected main takeoff: the system main positive is taken from one end of the parallel set and the system main negative from the opposite end. This keeps the path resistance closer for both packs than landing both main conductors on the same battery, and should reduce unequal current sharing and SOC/cell-balance drift between packs.

Document or confirm:

- Per-battery overcurrent protection.
- Main DC disconnect.
- Cable gauge and length.
- Torque specs, if available.
- Grounding/bonding arrangement — see [Grounding And Bonding](#grounding-and-bonding).

## Grounding And Bonding

Reference: MagnaSine **MS4448PAE** installation manual §2.2 (`manuals/solar/Magnum-Inverter-Charger-MS4448PAE.pdf`).

**Current state (2026-06): the DC negative is NOT bonded to ground — the system runs floating.** This is out of spec for the MS4448, which is chassis-isolated and includes **no internal system bond** (manual §2.2.2: *"does not include an internal bond between the Grounded Conductor (AC neutral / DC negative) and the equipment grounding terminals … usually done in the main distribution panel"*). Grounding is the installer's responsibility, so both bonds must be made externally. Grounding is invisible in normal operation and only acts during a fault; "behaving as expected" is not evidence it is correct.

**Single-point rule (manual §2.2.2):** exactly **one** point in each electrical system where the grounded conductor ties to ground — one AC neutral-ground bond, one DC negative-ground bond. Multiple bonds create parallel current paths (a safety hazard, and a noise source relevant to the Magnum RS485 link).

Intended scheme (single shared ground bus → grounding electrode/rod; ~Method 2):

| Conductor | Run | Carries fault current? | Size |
| --- | --- | --- | --- |
| Battery cable | battery ↔ inverter | — (operating current) | **4/0** installed (oversized; Table 2-3 base is #2/0 for ≤5 ft) |
| DC OCPD | in the **positive** line | n/a | **175 A Class-T**, DC-rated, **bidirectional** (manual: fuse "can be energized from both directions") |
| DC SBJ (system bond) | DC-negative bus → ground bus | **yes** | **#2 min; run #2/0–4/0 to match cable** |
| DC EGC | inverter case → ground bus | **yes** | **#4** (base #6 per Table 2-2 @ 175 A, upsized proportionally because the cable is oversized to 4/0) |
| AC SBJ (system bond) | neutral bus → ground bus at the **main AC panel** | **yes** | **#8** (Table 2-1, by AC hot conductor) |
| GEC | ground bus → earth rod | **no** (earth reference only) | **#6** — capped regardless of cable size; the rod is a voltage reference, not a fault path |

Principle that sets the gauges: every conductor that can carry **fault current** (both SBJs, both EGCs) is sized to the fault it must survive until the OCPD clears, and scales up when the power cable is oversized; the **GEC to the rod never scales** (fault current returns through copper, not earth). The DC OCPD and the bidirectional-fuse requirement also confirm the separate finding that **battery-leg breakers must be bidirectional** (polarized DC MCBs like the TOMZN TOB1Z are wrong here) — see [journal 2026-06-11-a](../journal/2026-06-11-a.md).

**Generator/shore caveat:** the MS4448 has **no internal neutral-ground transfer relay**, so the AC bond at the main panel is permanent. A generator with its own **bonded** neutral would create a second N-G bond during pass-through (parallel neutral paths). Use a **floating-neutral generator** (or unbond it). Confirm the genset's neutral configuration before finalizing.

Equipment grounding (all chassis — inverter, both charge controllers, battery enclosure metal, PV frames — to the ground bus and out to the rod) is required regardless of the bond decision; the inverter has a dedicated DC equipment-ground terminal for this.

Gauges above are derived from the manual's NEC tables; **confirm against the current CEC** (this is a Canadian install) before committing copper. The principle is stable; a gauge step may differ.

## Open Questions

- What exact chemistry and BMS model are used in the Cubix 100?
- Can each battery provide telemetry independently?
- Is a separate WhizBang Jr / shunt monitor useful enough to consume Classic AUX2, or should AUX2 be preserved for charger control?
- What are the manufacturer limits for parallel operation?
- What final cable gauge, cable length, terminal torque, and per-pack overcurrent protection are installed for the diagonal parallel wiring?
- Does CAN expose full telemetry, or only inverter-oriented battery summary/status?
- Is RS485 a better first integration path for Pi-based monitoring?
- What voltage/SOC thresholds should define full-charge, charge stop, and charge restart for this exact battery model?
- Does the BMS expose a charge-allowed or charge-disconnect warning before opening its internal MOSFETs/contactors?
