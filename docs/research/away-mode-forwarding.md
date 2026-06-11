# Away-Mode Log Forwarding via Scheduled Inverter Wake

Status: design note / planned feature (not built). Likely graduates to an
ADR when committed. This is the concrete application that justifies
building the Magnum inverter on/off write path banked in
[decision 0002](../decisions/0002-magnum-remote-takeover.md).

## Goal

When the cabin is unoccupied, keep the inverter **off** to conserve the
bank, and periodically wake it to power the Starlink terminal long enough
to forward stored telemetry (the existing R2/S3 store-and-forward export),
then shut the inverter back off.

## Why this is worth it — the real win is inverter idle draw

The headline value is *not* the Starlink scheduling; it is eliminating the
inverter's standby consumption while unoccupied:

- A MagnaSine idling "on" with no load still draws roughly 25-50 W. Over a
  week that is ~15 Ah/day at 48 V of pure waste against the 200 Ah bank.
- A wake cycle is cheap by comparison: Starlink (~50-75 W) plus inverter
  overhead for ~15-20 min (boot + satellite acquire + export) is on the
  order of 25-35 Wh, well under 1 Ah. Even daily, the forwarding cost is
  negligible next to the idle draw it avoids.

So the feature is really "inverter off while away," with periodic wakes for
remote visibility riding on top.

## Critical enabler (already true)

The **Pi is DC-powered** — Mean Well DDR-60L-5 (48 V → 5 V) off the battery
bus, not an inverter AC outlet. So the supervisor survives inverter-off and
can wake it again. This is a hard dependency: never move the Pi (or its CAN
adapter, or the Magnum RS485 tap) onto inverter-fed power, or the cabin
goes dark with no way back.

## Mechanism

- **Actuator:** the Magnum inverter on/off toggle, proven to work with the
  ME-RC50 installed (the remote does not fight it — see the toggle test in
  [magnum-inverter-interface.md](magnum-inverter-interface.md)). The tap is
  read-only by policy today; this feature flips that for the toggle only.
  Use the state-aware path from that note: read state, toggle only if a
  change is needed, re-read to confirm, retry, rate-limit.
- Toggling the **whole inverter** (not a Starlink-only relay) is the
  correct choice precisely because the inverter's own idle draw is the
  thing being saved. A dedicated Starlink relay would not recover that.

## Sequencing (state machine sketch)

1. Precondition gate: in away mode AND battery SOC above a floor (don't
   spend scarce winter charge on forwarding; skip the cycle if low).
2. Toggle inverter ON; verify it came on (status readback).
3. Wait for Starlink link — poll for WAN reachability rather than a fixed
   timer (cold boot + acquisition is minutes and variable).
4. Run the R2/S3 export; confirm it drained the unsent queue.
5. Toggle inverter OFF; verify it went off; retry if not.
6. Log the cycle (duration, Wh estimate, records forwarded) for autonomy
   accounting.

## Open questions

- **Occupancy signal.** Start with an explicit manual "away mode" flag set
  before leaving — simplest and safest. Inferring occupancy from load
  patterns is a later refinement, not a v1.
- **Starlink AC topology.** Confirm Starlink is the only/primary AC load
  when away (so cycling the whole inverter is acceptable), and how it
  behaves across hard power cycles (boot time, dish heater inrush in
  winter).
- **Link-up detection.** What does the supervisor poll to know Starlink is
  online — ping a known host, check the dish's local status endpoint?
- **Fail-safe direction.** A missed OFF toggle wastes idle power (not
  dangerous); a spurious OFF when occupied loses comms. Neither is
  catastrophic, but the state-aware readback + retry should bias toward
  the operator's intent and alert on repeated toggle failures.
- **Winter dish power.** Starlink's self-heat can be 50-100+ W; a wake in
  deep cold costs more and may be worth skipping or rate-limiting on SOC.

## Dependencies before building

- Build and bench-verify the Magnum toggle write path (state-aware,
  rate-limited) on the KL0823B — note its CH340 auto-direction is fine for
  one-shot toggles (see inventory note).
- An away-mode flag and a scheduler (systemd timer or supervisor-internal).
- Confirm the R2 export exposes a "queue drained" signal the loop can wait on.
