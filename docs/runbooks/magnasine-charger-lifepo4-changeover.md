# MagnaSine Charger LiFePO4 Changeover

Use this runbook when changing the Magnum/MagnaSine inverter-charger from legacy lead-acid charger settings to settings compatible with the Eco-Worthy Cubix 100 LiFePO4 battery bank.

This is lower urgency than the MidNite Classic solar-charge changeover because AC charging only happens when AC input or the generator is manually available. It still matters: if generator charging is enabled with old lead-acid settings, the inverter/charger can push the LiFePO4 bank into an inappropriate absorb, float, or equalize profile.

## Current Status

Known:

- Product family: MagnaSine.
- Model marking in project notes: `4448`.
- Installed remote: Magnum `ME-RC50`.
- Charger settings are expected to be managed manually through the ME-RC50 until a supported/verified data interface exists.
- The Magnum network path is not yet trusted for writes or automated control.
- The ME-RC manual for revision 2.8+ documents `SETUP: 04 Battery Type` choices including `CC/CV` and `Custom`.
- The Magnum LFP quick-reference guide applies to `-L` inverter/charger models and `ME-RC-L`/`ME-MR-L` remotes. The current project inverter is not yet confirmed as an `-L` model, so do not assume a native `LFP` battery type exists.

Unknown:

- Exact inverter model string and firmware.
- Existing lead-acid charger settings.
- Battery type/profile options exposed by the installed ME-RC50 revision.
- Whether charger standby/off can be commanded independently of inverter operation.
- Whether equalization is disabled and how to verify it.
- No automatic generator start is installed; generator charging is manual.

## Pre-Change Checklist

1. Confirm exact inverter/charger model label.
2. Confirm ME-RC50 revision and firmware from the remote's TECH menu.
3. Confirm whether `SETUP: 04 Battery Type` offers `CC/CV`, `Custom`, or `LFP`.
4. Record all existing charger, battery-capacity, and low-voltage settings before changing anything.
5. Confirm whether any Magnum battery-monitor accessory is installed, such as ME-BMK.
6. Confirm AC input/generator wiring and input breaker/disconnect path.
7. Confirm the generator is off and AC input is unavailable before changing charger settings.
8. Confirm the MidNite Classic solar charge settings have also been addressed, or solar charging is disabled during the battery swap.

## Record Current Lead-Acid Settings

Using the ME-RC50, record at least:

| Setting | Current value | Notes |
|---|---:|---|
| TECH: 02 Revisions | TBD | Record remote, inverter, and accessory revisions shown |
| TECH: 03 Inv Model | TBD | Record exact model reported by the remote |
| SHORE: Shore Max | TBD | Limits AC input draw from generator/grid |
| SETUP: 02 LowBattCutOut | TBD | Inverter low-voltage shutdown threshold |
| SETUP: 03 Absorb Time | TBD | May be hidden if Battery Type is CC/CV |
| Battery Ah / bank capacity | TBD | Record any remote/accessory battery-capacity setting |
| Battery type/profile | TBD | Save exact displayed value |
| Custom Absorb voltage | TBD | Record if Battery Type is Custom |
| Custom Float voltage | TBD | Record if Battery Type is Custom |
| Custom Equalize voltage/time | TBD | Record if Battery Type is Custom |
| CC/CV Max Amps | TBD | Record if Battery Type is CC/CV |
| CC/CV Charge Volts | TBD | Record if Battery Type is CC/CV |
| CC/CV End Charge mode | TBD | Record Time, DC Amps, or Hold VDC |
| CC/CV DoneTime / DoneAmps | TBD | Record active end-charge setting |
| CC/CV MaxTime | TBD | Safety cap for CC/CV charging |
| CC/CV Recharge voltage | TBD | Restart threshold from Silent |
| SETUP: 05 Charge Rate | TBD | May display `CC/CV Controlled` if Battery Type is CC/CV |
| SETUP: 09 Final Charge | TBD | May display `CC/CV Controlled` if Battery Type is CC/CV |
| Charger mode / standby setting | TBD | Confirm how to disable charging while preserving inversion if possible |

Keep this table as the rollback reference for the old lead-acid bank. Do not apply the rollback settings to the Cubix 100 bank.

## Change To LiFePO4-Compatible Settings

Use the ME-RC50 or other supported Magnum configuration method. Do not use reverse-engineered Magnum network writes for the first changeover.

This is a full battery-profile change, not only a charger-voltage change. Update bank Ah/capacity, battery type, charger profile, low-voltage cutout, final-charge behavior, rebulk/recharge behavior, shore/input current limit, and any ME-BMK settings that use battery capacity or voltage thresholds.

### Common Battery Profile Targets

Apply these targets regardless of whether the final charger path is `CC/CV`, `Custom`, or confirmed native `LFP`:

| Setting | Candidate value | Notes |
|---|---:|---|
| Battery Ah / bank capacity | 200 Ah nominal | Two Cubix 100 batteries in parallel; update ME-RC, ME-BMK, or any accessory that stores bank Ah |
| SETUP: 02 LowBattCutOut | 48.0 V initial planning value | Different from lead-acid; voltage-only LBCO is crude for LiFePO4, so tune after observing loaded voltage and BMS behavior |
| SHORE: Shore Max | TBD | Set for the generator/input breaker so charging and pass-through loads do not overload AC input |
| Manual generator procedure | TBD | No AGS is installed; document when to start/stop generator charging based on SOC, voltage, and weather |
| SOC thresholds | TBD | If ME-BMK exists, reset/recalibrate around the new bank rather than trusting old SOC state |

### Preferred Path: CC/CV If Available

If `SETUP: 04 Battery Type` offers `CC/CV`, prefer it for the first LiFePO4 generator-charging setup. The ME-RC manual describes CC/CV as a two-stage Constant Current / Constant Voltage profile. When the end-charge condition is met, the charger transitions to `Silent`, where it stops actively charging and waits until battery voltage falls to the `Recharge` value before starting a new cycle. This is a better fit for LiFePO4 than indefinite float.

Use these initial planning targets unless Eco-Worthy or Magnum documentation requires otherwise:

| Setting | Candidate value | Notes |
|---|---:|---|
| SETUP: 04 Battery Type | CC/CV | Preferred if available on this inverter/remote combination |
| CC/CV Max Amps | 40 A initially | Conservative generator-charging start point; increase only after wiring, generator capacity, and battery behavior are verified |
| CC/CV Charge Volts | 55.2 V | Conservative 48 V LiFePO4 starting point; consider up to 56.0 V only after first-cycle testing |
| CC/CV End Charge | Time / DoneTime | Use time first; avoid DoneAmps unless a reliable battery monitor measurement is available |
| CC/CV DoneTime | 0.1 hr | ME-RC minimum displayed increment is 0.1 hr, about 6 minutes |
| CC/CV MaxTime | 1.0 hr | Safety cap for the first generator-charge tests; adjust only after observed behavior is understood |
| CC/CV Recharge | TBD, below normal resting voltage | Choose after observing resting/load behavior; avoid generator-charge chatter |

### Fallback Path: Custom + Silent If CC/CV Is Unavailable

If `CC/CV` is not available but `Custom` is available, use Custom battery type and set `SETUP: 09 Final Charge` to `Silent` if the remote supports it.

| Setting | Candidate value | Notes |
|---|---:|---|
| SETUP: 04 Battery Type | Custom | Use only if CC/CV is unavailable |
| SETUP: 03 Absorb Time | 0.1 hr if allowed, otherwise the lowest available value | ME-RC manual notes some inverter revisions may force a 1.0 hr minimum despite lower displayed settings |
| Custom Absorb voltage | 55.2 V | Conservative 48 V LiFePO4 starting point; consider up to 56.0 V only after first-cycle testing |
| Float voltage | 53.6-54.0 V | Avoid sustained high float |
| Custom Equalize voltage | 55.2 V if the UI requires a value | Magnum says Equalize cannot be set lower than Absorb; set equal to Absorb if possible so an accidental EQ request is not higher voltage |
| Custom Equalize time | Lowest available value | Do not intentionally start Equalize on LiFePO4 |
| SETUP: 05 Charge Rate | 40 A equivalent if calculable, otherwise conservative percent | This menu is a percentage of the inverter/charger's maximum charge current |
| SETUP: 09 Final Charge | Silent | Stops active charging after Absorb and restarts only at Rebulk |
| Silent Rebulk | TBD, below normal resting voltage | Choose after observing resting/load behavior; avoid generator-charge chatter |

### Native LFP Path If Confirmed

If the remote offers `LFP`, stop and confirm the inverter/remote is one of the Magnum `-L` lithium-compatible combinations. Magnum's LFP quick-reference guide says compatible ME-RC-L revision 2.9+ setups use:

- Low Battery Cutout from battery manufacturer specs, or `11.0 V` on 12 V systems if unspecified.
- Absorb Time from manufacturer specs.
- Battery Type `LFP`.

For a 48 V system, convert any 12 V example values by multiplying by four. Do not assume the generic `LFP` option is available or appropriate until the exact model and remote revision are confirmed.

## Verification

1. With the generator off and no AC input present, save the settings through the ME-RC50.
2. Re-open each settings screen and confirm values persisted.
3. Confirm equalize is disabled.
4. Confirm charger mode can be disabled manually before starting the generator. The ME-RC has separate inverter and charger ON/OFF buttons; verify locally that disabling the charger does not disable required inverter output.
5. During the first generator charge session, monitor:

   - Battery voltage from the Cubix BMS/ESM-100.
   - Charger state on the ME-RC50.
   - Charge current.
   - Battery temperature.
   - Any BMS alarms or charge-disallow state.
   - Generator loading and AC input voltage.

6. Stop generator charging if the bank approaches the documented voltage or temperature limits, or if the BMS reports charge not allowed.
7. After the first session, record observed behavior:

   | Observation | Value |
   |---|---:|
   | Highest bank voltage | TBD |
   | Highest charge current | TBD |
   | Time at charge voltage | TBD |
   | Final charger state | TBD |
   | Cubix SOC before/after | TBD |
   | Any BMS alarm/fault | TBD |

## Roll Back To Lead-Acid Settings

Use this only if the old lead-acid bank is reinstalled.

1. Confirm the lead-acid batteries are physically connected and the Cubix batteries are not connected to the inverter/charger DC terminals.
2. Reapply the recorded lead-acid settings through the ME-RC50.
3. Re-enable lead-acid behavior, including equalization, only if it matches the old battery manufacturer's requirements and the system is physically back on lead-acid batteries.
4. Verify charger behavior during the next generator/AC-input session.

## Future Automation Requirements

A future Magnum control script must be treated as higher risk than read-only monitoring until the protocol and interaction with the ME-RC50 are proven.

Requirements before any automated writes:

- Passive telemetry capture works reliably.
- The exact Magnum command set is documented for this inverter/remote combination.
- Writes are tested off-system or with charging disabled.
- Charger standby/off is proven independent from inverter output behavior.
- Settings writes are allowlisted, bounded, logged, and read back.
- Manual ME-RC50 control remains the trusted override path.

## Source Notes

- ME-RC Owner's Manual Rev G documents `SETUP: 02 LowBattCutOut`, `03 Absorb Time`, `04 Battery Type`, `05 Charge Rate`, and `09 Final Charge`.
- ME-RC Owner's Manual Rev G documents `CC/CV` and `Custom` battery type behavior. In `CC/CV`, `03 Absorb Time`, `05 Charge Rate`, and `09 Final Charge` are controlled by the CC/CV profile.
- ME-RC Owner's Manual Rev G documents `Silent` final-charge behavior: charging stops after Absorb and restarts at Rebulk.
- Magnum's LFP quick-reference guide applies to listed `-L` inverter/charger models and lithium-specific remotes; use it only if the installed hardware matches.
