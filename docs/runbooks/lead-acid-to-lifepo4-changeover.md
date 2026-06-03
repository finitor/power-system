# Lead-Acid To LiFePO4 Changeover

Use this runbook when replacing the current legacy lead-acid bank with the Eco-Worthy Cubix 100 LiFePO4 battery bank.

The first changeover should be done from the MidNite Classic front panel or MidNite local application, with Modbus used for readback verification only.

Do not use raw Modbus writes for the first battery swap. The Classic Modbus map exposes writable charge-setting registers, but MidNite warns that raw writes can damage connected equipment if the wrong register or value is written. Add a separate guarded writer only after the manual process is proven and the target values are final.

## Current Lead-Acid Baseline

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

## Pre-Change Checklist

1. Confirm the exact Cubix 100 model and manual revision.
2. Confirm Eco-Worthy's published charging limits for this exact battery. Current project research shows nominal voltage `51.2 V`, max voltage range `40-58.4 V`, charge voltage `58.4 V`, standard charge current `50 A`, max charge current `100 A`, and BMS charge overvoltage protection at `58.4 V` with recovery at `54.4 V`.
3. Record any Classic battery-capacity, battery-monitor, low-voltage, rebulk, or auxiliary settings that were tuned for the old lead-acid bank.
4. Decide conservative target values before touching the Classic. Do not use the BMS protection voltage as a routine operating target unless that is a deliberate first-cycle/top-balance procedure.
5. Disable equalization for normal operation.
6. Decide how float should be handled. For this LiFePO4 retrofit, prefer no sustained high float; use the lowest safe maintenance/rest behavior the Classic can support, and later use AUX2/Logic Input 1 or supervisory control to stop charging after a full-charge condition if testing supports it.
7. Confirm battery temperature is above the charge-allowed threshold before enabling solar charging.
8. Confirm the Magnum inverter/charger is not simultaneously applying incompatible lead-acid charge settings.

## Change To LiFePO4 Settings

1. Save a before snapshot:

   ```sh
   source .venv/bin/activate
   python scripts/classic-probe.py --host 192.168.0.10 --raw
   ```

2. Open the Classic front panel or MidNite local application.
3. Change the battery type/profile/settings manually. This is a full battery-profile change, not only a charge-voltage change. Update bank capacity, charge profile, equalization behavior, rebulk/restart behavior, and any low-voltage thresholds that were tuned for the old lead-acid bank.

   MidNite's archived Classic lithium guidance says to use manufacturer/BMS requirements for Absorb, use a short Absorb time, set Float low enough that the battery is not held at a high charge voltage, disable Equalize, and use Rebulk to restart charging later. Current Classic baseline targets:

   | Setting | Candidate value | Notes |
   |---|---:|---|
   | Battery/bank capacity | 200 Ah nominal | Two Cubix 100 batteries in parallel; update any Classic or accessory setting that uses bank Ah |
   | Absorb voltage | 55.6 V | Midpoint between the first conservative setting and the supervised elevated top-off setting; observed benignly twice |
   | Absorb time | 1950 s | Midpoint between the first 5-minute setting and the supervised 1-hour top-off setting |
   | Float voltage | 55.0 V | Midpoint setting intended to let the pack finish more consistently without holding the full elevated float target |
   | Rebulk voltage | TBD, below float | Choose after observing resting voltage and load behavior; avoid chatter between float/resting and bulk |
   | Low-voltage thresholds | TBD for LiFePO4 | If the Classic or any accessory has low-voltage alarms/load-control thresholds, do not carry over lead-acid values blindly |
   | Equalize | Disabled | Do not equalize LiFePO4 in normal operation |
   | Equalize voltage/time/interval | 55.6 V / disabled | Prevent accidental equalize entry; keep any EQ voltage no higher than the normal absorb target if the UI requires a value |
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

## Roll Back To Lead-Acid Settings

Use this only if the old lead-acid bank is reinstalled or the LiFePO4 changeover is abandoned. Do not apply these settings to the Cubix 100 bank.

1. Confirm the lead-acid batteries are physically connected and the Cubix batteries are not connected to the Classic output.
2. Reapply the baseline settings from the table above using the Classic front panel or MidNite local application.
3. Re-enable any lead-acid behavior that was deliberately disabled for LiFePO4, such as the old absorb/float/equalize policy, only if it matches the old battery manufacturer's requirements.
4. Run:

   ```sh
   python scripts/classic-probe.py --host 192.168.0.10 --raw
   ```

5. Confirm the readback matches the lead-acid baseline or the updated documented lead-acid target.

## Future Modbus Writer Requirements

A future script may write charge settings over Modbus, but it must be a separate tool from `classic-probe.py` and must include these guardrails:

- Dry-run mode by default.
- Explicit battery profile name, such as `eco-worthy-cubix-100`.
- Allowlist only the intended registers.
- Conservative min/max validation for every written value.
- Required operator confirmation showing old value, new value, raw register, and scaled value.
- Readback verification after every write.
- Separate `--persist` flag before issuing any EEPROM-save force flag.
- No force-bulk, force-float, force-equalize, or reset-fault commands in the same tool.
