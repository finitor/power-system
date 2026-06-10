# Magnum Inverter Interface Research

## Context

The inverter/charger appears to be a Magnum MS4448PAE-class unit with an ME-RC50 remote. The existing phone-style remote cable indicates that the inverter is using Magnum's proprietary remote/accessory network.

## Current Hardware Status

Dedicated pilot adapter received 2026-06-09: DSD TECH SH-U11H USB-RS485/422 industrial isolated converter. Uses a Prolific PL2303 chip; enumerates as `/dev/ttyUSB1` on blueberry.

Use this adapter only for the Magnum network pilot. Do not share it with the battery RS485 fallback bus.

## Prior Art

### pyMagnum

Project: https://pymagnum.readthedocs.io/

Notes:

- Describes the Magnum Energy network as a proprietary protocol carried over RS485.
- States that the inverter sends a packet roughly every 100 ms and the remote replies with settings and optional commands.
- Identifies fields such as inverter mode, fault, DC voltage/current, AC input/output values, charger state, temperatures, model, and accessory data.
- Treat as primarily read-oriented prior art.

### magnum-pi

Project: https://pypi.org/project/magnum-pi/

Notes:

- Newer async Python package for sniffing, decoding, and transmitting Magnum network packets.
- Claims the bus is two-wire RS485 at 19200 baud, 8N1.
- Documents connection to the Magnum Network RJ11 port or the daisy-chain port on an ME-RC remote.
- States pin 1 is Data+ and pin 4 is Data-.
- Recognizes 48 V MS-series models including MS4448PAE.
- Claims CLI support for sending inverter commands, including inverter toggle.

This is promising but should be treated as experimental until tested on the bench with the actual inverter and remote.

### MagWeb

Manual: https://www.magnum-dimensions.com/sites/default/files/product/manual/sensata-magweb-wired-ethernet-monitoring-kit-mw-owner-manual.pdf

Notes:

- Official Magnum accessory path for web monitoring.
- Installs inline between the inverter remote port and the remote control.
- Uses the same four-conductor RJ11 remote cable on the inverter/remote side.
- May be useful if a compatible unit is obtainable, but cloud/service longevity and local API access need verification.

## Recommended Way Forward

1. Keep the ME-RC50 installed and working as the trusted manual control.
2. Add a separate isolated USB-RS485 adapter for Magnum network experiments instead of reusing the battery RS485 adapter.
3. Build an RJ11 breakout/test cable so the Pi can listen to the Magnum bus without disturbing the remote.
4. Start in listen-only mode with `pymagnum` or `magnum-pi` and confirm decoded model, mode, fault, DC voltage, AC values, charger state, and inverter on/off state.
5. Only after reliable passive decoding, test an explicit inverter toggle command in a controlled session with local access to the remote and AC loads disconnected or non-critical.
6. Expose Pi control as a guarded command, not an automatic policy at first.

## Hardware Implications

Add for pilot testing:

- Waveshare USB TO RS485/422 Industrial Grade Isolated Converter, on order expected 2026-06-05, dedicated to the Magnum network.
- RJ11 6P4C or 6P6C breakout connectors.
- Short RJ11 telephone patch cables.

Do not connect the Magnum RJ45 stack/router port to Ethernet. It is not an Ethernet port.

## Bench RJ45 Breakout Wiring

Current bench cable path: straight-through 4-wire RJ11 cable from the MagnaSine network port, into an RJ45 straight-through coupler, then Cat-6 patch cable to an RJ45 screw-terminal breakout.

Measured on 2026-06-05:

| RJ45 breakout pin | Expected signal | Measurement / note |
|---:|---|---|
| 3 | RS485 B / D- | 0.52-0.57 V to pin 5 |
| 4 | +14 V accessory power | 14.14 V to pin 5 |
| 5 | GND | Reference |
| 6 | RS485 A / D+ | 3.85-3.91 V to pin 5; 3.28-3.37 V to pin 3 |

Expected USB-RS485 adapter wiring for the first bench attempt:

| RJ45 breakout | USB-RS485 adapter |
|---|---|
| Pin 6 | A / D+ / 485+ |
| Pin 3 | B / D- / 485- |

Leave RJ45 pin 4 (+14 V) and pin 5 (GND) disconnected from the USB adapter. If no packets decode, swap pins 3 and 6 at the adapter before assuming a protocol or software problem, since some adapters label A/B backward.

The SH-U11H has two unpopulated jumper positions labelled **120R T** and **120R R** (transmit-line and receive-line termination resistors). Leave both unpopulated. The ME-RC50 and the inverter are the two bus endpoints and own termination; the Pi is a passive mid-bus tap and must not add a third termination load.

## Packet Identification and Cycle Alignment Bug

The `magnum-pi` `CycleTracker` identifies the first packet in a cycle as the inverter if byte 10 is non-zero. When the Pi joins the bus mid-cycle it may receive the remote packet first. The remote's byte 10 is also non-zero (0x9B in the observed ME-RC50 firmware), so the tracker permanently misidentifies the two packets for the entire session.

Correct identification by known values (MS4448PAE, firmware rev 6.1):

| Byte position | Inverter packet | Remote packet |
|---|---|---|
| [10] revision | 0x3D = 61 → rev 6.1 | 0x9B = 155 |
| [14] | 0x73 = 115 = MS4448PAE model | 0x14 (unrelated) |

Detection approach: check byte 14 for 0x73 to confirm the inverter packet; the other 21/22-byte packet is the remote. A `MagnumClient` wrapper must verify alignment on connect and swap if inverted before using decoded values or sending writes.

## Voltage Encoding

The Magnum bus stores voltages as 12V-nominal × 10, single byte. For a 48V system (multiplier = 4):

- Decode: `actual_v = wire_byte × 4 / 10`
- Encode: `wire_byte = int(actual_v × 10 / 4)`
- Resolution: 0.4 V per wire count at 48V (0.1 V at 12V-nominal)

The ME-RC50 display steps voltages in 0.4V increments on a 48V system, which is one wire count. This was confirmed by observation: incrementing float on the remote changed the wire byte from 0x89 (54.8V) to 0x88 (54.4V).

The `magnum-pi` `voltage_multiplier` is not reliably auto-detected due to a race between `_identify_as_inverter_or_remote` (which sets `_inverter_model_id` from the raw byte) and `_parse_into_cycle` (which gates the multiplier update on `_inverter_model_id is None`). By the time parsing runs, the ID is already set, so the multiplier stays 1. Force multiplier = 4 in `MagnumClient` after confirming the model byte.

Remote packet field positions confirmed by live observation (2026-06-09):

| Byte | Field | Example value |
|---|---|---|
| 0 | Control flags (inverter/charger/eq toggle) | 0x00 (none active) |
| 3 | Custom absorb voltage (wire) | 0x89 = 54.8V |
| 5 | Shore/AC input current limit (A) | 0x1E = 30A |
| 11 | Float voltage (wire) | 0x89 = 54.8V |
| 13 | Absorb time (× 0.1 hr) | 0x1E = 3.0hr |

## Write Injection: ME-RC50 Override Problem

Confirmed 2026-06-09: the Pi can inject a valid remote packet onto the green network port and the inverter accepts it for one bus cycle. However the ME-RC50 retransmits its own saved settings every ~100ms. Our single injected packet is immediately overwritten.

**Consequence**: a parallel tap on the network port cannot hold a setting change against a live ME-RC50. One-shot writes do not persist.

**Options for sustained write control:**

1. **Remove the ME-RC50** — Pi owns the remote slot entirely. Loses manual control panel.
2. **Pi transmits every cycle (~100ms)** — two devices contending in the same time slot causes RS485 collisions. Not safe.
3. **Inline interposer (MagWeb topology)** — Pi sits between the inverter network port and the ME-RC50. Passes through ME-RC50 packets transparently; substitutes modified packets when an override is active. Requires two RS485 adapters (one per bus segment) and a relay loop in the Pi. This is the only architecture that allows Pi control while keeping the ME-RC50 in service.

Monitoring and read-only telemetry work fine from a parallel tap with no changes needed. For control, the provisional decision (see `docs/decisions/0002-magnum-remote-takeover.md`) is option 1 — remove the ME-RC50 and have the Pi take over the remote's function — with the supervisor's Inverter/Charger display group as the visibility validation step, and option 3 as fallback if the Pi cannot meet the availability bar.

## Custom CC/CV Settings Are Not Broadcast (2026-06-10)

The ME-RC50 has a Custom CC/CV battery profile active with a 40 A charge
current limit. Bench findings from bus captures:

- Steady-state remote broadcast cycles exactly three footer pages: 0x00
  (base), 0x80 (BMK), 0xA0 (AGS legacy). Within footer 0x00, bytes 16-19
  alternate between two sub-pages each cycle (observed `00 00 17 00` and
  `14 00 6e 00`; meaning not yet identified).
- `charger_amps_pct` (base byte 4) reads 0 while the custom profile is
  active — the standard charge-rate-% field is unused in custom mode.
- A live edit of the custom CC limit (40 → 50 → 40 at the panel) produced
  **zero observable bus traffic**: no changed bytes in any remote packet,
  no new packet types, no additional footer pages during a 3-minute
  capture spanning the edits.

So the inverter must learn the custom CC/CV parameters by some path other
than the continuous remote broadcast. Untested hypotheses, in rough order
of plausibility:

1. Sent once during the remote/inverter power-up or bus-join handshake.
2. Sent when a charge cycle actually starts (AC input present).
3. Request/response initiated by the inverter.

Cheap future tests: capture from the moment the remote is power-cycled
(briefly unplug its RJ11), and capture the bus when generator charging
begins.

**Implication for decision 0002 (remote takeover):** replacing the ME-RC50
requires replicating whatever mechanism conveys custom CC/CV — currently
unknown. This is a validation blocker to resolve before the remote comes
off the wall.

## Control Policy

For inverter on/off, use a state-aware command path:

- Read current inverter state from the bus.
- If the desired state already matches, do nothing.
- If a state change is needed, issue one toggle command.
- Re-read state and alert if it did not change.
- Rate-limit commands and require local/manual enable during early testing.

Avoid blind repeated toggles because the protocol exposes inverter on/off as a toggle-style command in the prior art.

Note: toggle commands face the same ME-RC50 override problem as parameter writes. A one-shot toggle may apply for one cycle and then be undone by the ME-RC50's retransmission of its own control byte. Sustained control requires the inline interposer topology.

## Open Questions

- Is a second RS485 adapter available or worth sourcing to implement the inline interposer topology?
- Does the ME-RC50 retransmit control flag state (inverter/charger toggle bits) in every packet, or only on change? If only on change, a one-shot toggle from the Pi might persist until the ME-RC50 sends a conflicting state.
- Can Magnum network transmit commands coexist safely with the ME-RC50 remote in normal service? (Answered for parameter writes: no, ME-RC50 wins. Open for toggle commands.)
