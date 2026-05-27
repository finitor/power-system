# Charge Controller

## Hardware

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

## Charge Setting Changeover Procedure

Use this procedure when replacing the current legacy lead-acid bank with the Eco-Worthy Cubix 100 LiFePO4 battery bank. The first changeover should be done from the Classic front panel or MidNite local application, with Modbus used for readback verification only.

Do not use raw Modbus writes for the first battery swap. The Classic Modbus map exposes writable charge-setting registers, but MidNite warns that raw writes can damage connected equipment if the wrong register or value is written. Add a separate guarded writer only after the manual process is proven and the target values are final.

### Current Lead-Acid Baseline

Read on 2026-05-27 from the Classic at `192.168.0.10` using `scripts/classic-probe.py`:

| Setting | Register | Current value |
|---|---:|---:|
| Battery current limit | 4148 | 40.0 A |
| Absorb voltage | 4149 | 59.2 V |
| Float voltage | 4150 | 54.0 V |
| Equalize voltage | 4151 | 64.8 V |
| Sliding current limit | 4152 | 400 A |
| Absorb time setpoint | 4154 | 7200 s |
| Maximum temperature-compensated voltage | 4155 | 64.8 V |
| Minimum temperature-compensated voltage | 4156 | 52.8 V |
| Temperature compensation | 4157 | -5.0 mV/C/cell |
| MPPT mode | 4164 | Solar, enabled |

Keep this table as the rollback reference for the old lead-acid bank. Before changing anything, run the probe again and save the output because the live settings may have changed since this baseline.

### Pre-Change Checklist

1. Confirm the exact Cubix 100 model and manual revision.
2. Confirm Eco-Worthy's published charging limits for this exact battery. Current project research shows nominal voltage `51.2 V`, max voltage range `40-58.4 V`, charge voltage `58.4 V`, standard charge current `50 A`, max charge current `100 A`, and BMS charge overvoltage protection at `58.4 V` with recovery at `54.4 V`.
3. Decide conservative target values before touching the Classic. Do not use the BMS protection voltage as a routine operating target unless that is a deliberate first-cycle/top-balance procedure.
4. Disable equalization for normal operation.
5. Decide how float should be handled. For this LiFePO4 retrofit, prefer no sustained high float; use the lowest safe maintenance/rest behavior the Classic can support, and later use AUX2/Logic Input 1 or supervisory control to stop charging after a full-charge condition if testing supports it.
6. Confirm battery temperature is above the charge-allowed threshold before enabling solar charging.
7. Confirm the Magnum inverter/charger is not simultaneously applying incompatible lead-acid charge settings.

### Change To LiFePO4 Settings

1. Save a before snapshot:

   ```sh
   source .venv/bin/activate
   python scripts/classic-probe.py --host 192.168.0.10 --raw
   ```

2. Open the Classic front panel or MidNite local application.
3. Change the battery type/profile/settings manually. MidNite's archived Classic lithium guidance says to use manufacturer/BMS requirements for Absorb, use a short Absorb time, set Float low enough that the battery is not held at a high charge voltage, disable Equalize, and use Rebulk to restart charging later. Initial conservative planning targets:

   | Setting | Candidate value | Notes |
   |---|---:|---|
   | Absorb voltage | 55.2 V | Conservative 48 V LiFePO4 starting point from MidNite forum guidance; consider up to 56.0 V only after first-cycle testing |
   | Absorb time | 5 minutes initially | MidNite FAQ suggests short absorb; Classic minimum is about 3 minutes; extend toward 15-30 minutes only if testing shows the bank is not reaching the desired full condition |
   | Float voltage | 53.6-54.0 V | Start low to avoid sustained high float; tune only if loads cause unwanted battery discharge while PV is available |
   | Rebulk voltage | TBD, below float | Choose after observing resting voltage and load behavior; avoid chatter between float/resting and bulk |
   | Equalize | Disabled | Do not equalize LiFePO4 in normal operation |
   | Equalize voltage/time/interval | Harmless/disabled values | Prevent accidental equalize entry; keep any EQ voltage no higher than the normal absorb target if the UI requires a value |
   | Temperature compensation | Lowest/neutral behavior available | LiFePO4 charging should be governed by BMS/external temperature limits, not lead-acid compensation; MidNite notes that leaving BTS active with minimum compensation can preserve high-temperature shutdown behavior |
   | Battery current limit | TBD, no more than battery and wiring limits | Two Cubix batteries can accept more current than one, but wiring, breakers, and charger behavior still set the limit |

4. Save settings through the Classic's normal UI.
5. Power-cycle or restart only if the Classic UI/manual requires it for the changed settings.
6. Run the probe again and compare every setting against the intended target:

   ```sh
   python scripts/classic-probe.py --host 192.168.0.10 --raw
   ```

7. Watch the first charge cycle locally. Confirm:

   - The Classic does not enter Equalize.
   - Battery voltage remains below the documented limit.
   - Battery BMS reports normal state with charge allowed.
   - Battery temperature remains inside the charge-allowed window.
   - The Classic leaves absorb/float according to the intended policy.

### Roll Back To Lead-Acid Settings

Use this only if the old lead-acid bank is reinstalled or the LiFePO4 changeover is abandoned. Do not apply these settings to the Cubix 100 bank.

1. Confirm the lead-acid batteries are physically connected and the Cubix batteries are not connected to the Classic output.
2. Reapply the baseline settings from the table above using the Classic front panel or MidNite local application.
3. Re-enable any lead-acid behavior that was deliberately disabled for LiFePO4, such as the old absorb/float/equalize policy, only if it matches the old battery manufacturer's requirements.
4. Run:

   ```sh
   python scripts/classic-probe.py --host 192.168.0.10 --raw
   ```

5. Confirm the readback matches the lead-acid baseline or the updated documented lead-acid target.

### Future Modbus Writer Requirements

A future script may write charge settings over Modbus, but it must be a separate tool from `classic-probe.py` and must include these guardrails:

- Dry-run mode by default.
- Explicit battery profile name, such as `eco-worthy-cubix-100`.
- Allowlist only the intended registers.
- Conservative min/max validation for every written value.
- Required operator confirmation showing old value, new value, raw register, and scaled value.
- Readback verification after every write.
- Separate `--persist` flag before issuing any EEPROM-save force flag.
- No force-bulk, force-float, force-equalize, or reset-fault commands in the same tool.

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
