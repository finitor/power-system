# Charge Controller

## Hardware

The system is moving toward multiple PV sources. Code and documentation should refer to charge-controller/PV-source telemetry generically where possible instead of assuming the Midnite Classic is the only solar input.

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
| PV array 0 | Midnite Solar Classic 200 | Canadian Solar CS6X-300-adjacent modules, 4s2p | Existing | 8 modules total; exact module ratings may vary around 295-305 W |
| PV array 1 | Victron BlueSolar MPPT 150/85 CAN-bus | Canadian Solar CS6X-300-adjacent modules, 4s3p | Dry run before mount construction | 12 modules total; exact module ratings may vary around 295-305 W |

## Telemetry Goals

| Measurement | Source | Priority | Notes |
|---|---|---|---|
| PV voltage | Classic 200 | High | Useful for array state and troubleshooting |
| PV current | Classic 200 | High |  |
| Charge output current | Classic 200 | High | Current into battery bus |
| Charge stage/state | Classic 200 | High | Bulk, absorb, float, resting, fault, etc. |
| Classic-local net battery current | WhizBang Jr, if installed | Low | Useful for end-amps logic and cross-checks, but consumes AUX2 and is not required for basic charge-stage visibility |
| Daily energy harvest | Classic 200 | Medium | Useful for system performance history |
| Controller temperature | Classic 200 | Medium | Watch thermal behavior |
| Faults/alarms | Classic 200 | High | Needs exact message mapping |

## Local Modbus Probe

The Classic is reachable on the LAN over Modbus TCP. Use the read-only probe script for quick local checks:

```sh
source .venv/bin/activate
python scripts/classic-probe.py --host 192.168.0.10 --raw
```

The script reads live telemetry and selected charge configuration registers. It must remain read-only unless a separate, reviewed control procedure is added.

## Changeover Runbook

Use [Lead-Acid To LiFePO4 Changeover](../runbooks/lead-acid-to-lifepo4-changeover.md) when replacing the current legacy lead-acid bank with the Eco-Worthy Cubix 100 LiFePO4 battery bank. Keep the procedure there because it is an action-oriented runbook with rollback steps, not just subsystem background.

## Control Boundaries

The charge controller owns charge regulation. The Raspberry Pi may monitor it and may later adjust non-critical settings only if the interface is reliable and the change is reversible from the controller front panel.

Because this is legacy equipment, assume its built-in charge stages may be lead-acid oriented until proven otherwise. The supervisor must specifically account for LiFePO4 behavior: bulk/absorb may be useful, sustained float should not become the normal long-term state after the bank is full, and equalization must be disabled for normal operation.

The Eco-Worthy ESM-100/BMS should be the first battery SOC/current source for the Pi. The Classic already reports its own charge stage, such as bulk, absorb, float, resting, or fault, without a WhizBang Jr.

Avoid or defer WhizBang Jr unless its benefits clearly outweigh the loss of AUX2. The WhizBang Jr uses Classic AUX2, and AUX2 may be more valuable as a control channel for high-level charge inhibit or other Classic functions in this LiFePO4 retrofit.

## AUX2 Input Functions

AUX2 can be configured as either an output/input port for Classic auxiliary functions or as the WhizBang Jr current-shunt input. These uses are mutually exclusive in normal planning: using AUX2 for WhizBang Jr means it is not available as a simple charge-control input.

Known AUX2 input functions from the Classic documentation:

| AUX2 function | Input behavior | Project relevance |
|---|---|---|
| WhizBang Jr | Uses AUX2 for the external shunt accessory | Provides Classic-local net battery current and ending-amps support, but duplicates battery voltage/current/SOC already expected from the Eco-Worthy BMS/ESM-100 path |
| Force Float | Input above roughly 6 V forces Float | Not ideal as the primary LiFePO4 full-charge behavior because the desired state after absorb is usually Resting/Stop Charge, not continued float |
| Logic Input 1 | High input forces Resting/Stop Charge; low input allows Charge | Strong candidate for a hardwired charge-inhibit path from the supervisor or battery protection logic |
| Logic Input 2 | High input forces Charge; low input forces Resting/Stop Charge | Potentially useful, but less fail-safe unless the external circuit is deliberately designed so faults land in the desired conservative state |

For this system, preserve AUX2 for Logic Input 1 research unless testing shows a better control path. The likely control pattern is: use battery/BMS telemetry and Classic charge-stage telemetry to decide when absorb is complete, then assert AUX2 Logic Input 1 so the Classic stops charging instead of maintaining a lead-acid-style float. Release the inhibit only after the battery falls below a documented recharge threshold and temperature permits charging.

Possible future supervisory actions:

- Alert when the controller reports a fault.
- Alert when expected solar production is absent.
- Record charge-stage history.
- Compare charge-controller battery voltage with battery-bank telemetry.
- Keep AUX2 available for charge-inhibit/control research unless WhizBang Jr is deliberately selected.
- Compare Classic-local net battery current, if WhizBang Jr is installed, against BMS/ESM-100 values.
- Detect excessive time in absorb or float.
- Alert immediately if the controller enters equalize.
- Keep equalize disabled or locked behind a deliberate manual procedure.
- If Modbus write control is confirmed safe, move the Classic to a resting/off/reduced-charge state after a full-charge condition.
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

- What communication interface is available on this specific Classic 200?
- Is the controller already on Ethernet?
- What firmware version is installed?
- Which values are available without cloud services?
- Are there existing local tools or Modbus maps worth using?
- Can the Classic be safely commanded out of float/rested through Modbus TCP?
- What LiFePO4-safe absorb voltage, absorb duration, float voltage, and rebulk/restart threshold should be used for the Eco-Worthy bank?
- Is WhizBang Jr useful enough for ending-amps control or current cross-checking to justify consuming AUX2?
- Which Classic functions can AUX2 support for charge inhibit or other high-level control?
- How is equalization disabled in the Classic configuration, and can the supervisor verify that state?
- Can Classic Mode Off or current-limit control be issued quickly and reliably enough to prevent BMS charge disconnect?
- Is a DC-rated PV input contactor/disconnect needed as a hardware fallback for charge inhibit?
