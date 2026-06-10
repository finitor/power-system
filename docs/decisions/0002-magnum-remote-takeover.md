# 0002: Replace the ME-RC50 Remote with Pi Control of the Magnum Network

Date: 2026-06-09

## Status

Rejected 2026-06-10 — keeping the ME-RC50; Magnum stays read-only from the Pi

Rationale for rejection: Magnum charging is generator-driven and therefore
always human-attended — the operator starts a small generator outside, so
charge sessions are observable and manually supervised by construction. The
risk that motivates closed-loop charge control is *unattended* charging,
which is the solar path; supervisory effort goes to the charger current
taper on the solar controllers instead. The discovery that Custom CC/CV
parameters are not conveyed in the remote's broadcast (see research note)
made takeover costlier at the same time as its benefit shrank.

The Pi keeps full read-only Magnum telemetry. Inverter on/off control, if
ever wanted, would reopen this decision or use the interposer fallback.

## Context

The Pi reads the Magnum inverter/charger over RS-485 from a parallel tap on
the inverter's green network port (DSD TECH SH-U11H adapter). Bench testing
on 2026-06-09 confirmed:

- Passive telemetry decoding works reliably (inverter status, DC/AC values,
  temperatures, remote charge settings).
- The Pi can transmit a valid remote packet and the inverter accepts it —
  a float/absorb voltage write was observed to apply.
- The ME-RC50 retransmits its own saved settings every ~100 ms, so any
  Pi-injected setting is overwritten within one bus cycle. One-shot writes
  cannot hold against a live remote.

Sustained Pi control therefore requires one of:

1. Removing the ME-RC50 so the Pi owns the remote slot.
2. The Pi transmitting every cycle in contention with the ME-RC50 — rejected
   outright as an RS-485 collision risk.
3. An inline interposer (MagWeb topology) between inverter and remote —
   requires a second RS-485 adapter and relay logic in the Pi.

Supervisory control of the Magnum charger is wanted for LiFePO4 charge
management (see `docs/architecture.md`): inverter enable/disable on low
battery, and charge parameter adjustment closed-loop on BMS cell data.

## Decision

Provisionally plan to remove the ME-RC50 entirely and have the Pi take over
the remote's function on the Magnum network (option 1), rather than build
the interposer.

Before committing, validate that Pi-side visibility is good enough to live
without the physical remote. The first step is the supervisor's
Inverter/Charger display group (added 2026-06-09 across web, terminal, and
API surfaces) showing inverter state, charge state, AC/DC values, and key
charge settings from the live bus.

## Consequences

- The Pi becomes the only way to see and change inverter settings. The
  supervisor display must reach parity with the ME-RC50 readouts the
  operator actually uses before the remote comes off the wall.
- The Pi must transmit the remote packet on the bus cadence (~100 ms slave
  slot) continuously, not just on changes — the inverter expects a remote
  responder. This is a service-reliability bar the current read-only
  supervisor does not yet meet; inverter behavior with no remote present at
  all must also be characterized (it runs standalone today when the
  supervisor is down, since the ME-RC50 is still installed).
- Toggle-not-set command semantics (inverter/charger toggles in byte 0)
  demand a state-aware command path with readback verification.
- The ME-RC50 stays on hand as bench fallback and for firmware-level
  settings the network protocol may not expose.
- If validation shows the Pi cannot meet the availability bar, fall back to
  the interposer topology (option 3) and order a second RS-485 adapter.
- Open blocker (2026-06-10): the active Custom CC/CV profile's parameters
  are not carried in the remote's continuous broadcast at all — a live
  panel edit of the CC limit produced no bus traffic (see research note).
  The takeover cannot proceed until the conveyance mechanism (power-up
  handshake, charge-start exchange, or request/response) is identified and
  reproducible from the Pi.
