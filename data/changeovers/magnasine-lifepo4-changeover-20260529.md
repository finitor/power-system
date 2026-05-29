# MagnaSine LiFePO4 Changeover Notes

Date: 2026-05-29

## Old Battery Bank Context

- Previous bank: 8 golf-cart lead-acid batteries in series.
- Nominal capacity: approximately 235 Ah.
- Age: approximately 9 years in service.
- Observed condition: capacity was noticeably degraded.

Because the old bank was aged and degraded, the old lead-acid settings should not be treated as ideal targets unless the old bank is physically reinstalled and its current condition is considered.

## New Battery Bank

- New bank: 2x Eco-Worthy Cubix 100 LiFePO4 rack batteries in parallel.
- Nominal voltage: 51.2 V.
- Nominal capacity: 200 Ah.

## MagnaSine Settings After Manual Update

These settings were changed manually through the Magnum ME-RC remote while the generator/AC charger was not expected to be used immediately.

| Setting | New value | Notes |
|---|---:|---|
| LowBattCutOut | 48.0 V | Intended to stop inverter before BMS low-voltage cutoff. |
| Absorb Time | 0.1 hr | About 6 minutes. |
| Battery Type | CC/CV | Constant-current / constant-voltage profile. |
| CC/CV Max Amps | 60 | Intended conservative maximum for the Magnum charger path. |
| CC/CV Charge Volts | 55.2 V | Conservative initial LiFePO4 charge-voltage target. |
| CC/CV End Charge | Time | Use time-based end condition initially. |
| CC/CV DoneTime | 0.1 hr | About 6 minutes. |
| CC/CV MaxTime | 1.0 hr | Safety cap for a CC/CV charge cycle. |
| CC/CV Recharge | 52.0 V | Initial recharge threshold. |
| Charge Rate | CC/CV Controlled | Displayed by remote after selecting CC/CV. |
| VAC Dropout | 80 VAC assumed/default | Confirm if needed; 80 VAC is appropriate for generator-tolerant operation. |

## Rollback Notes

Do not restore lead-acid-style settings unless the old lead-acid bank is physically reinstalled or another compatible lead-acid bank is installed.

The exact previous MagnaSine settings were not electronically captured before manual changes. If rollback is needed, configure explicitly for the installed lead-acid bank rather than assuming the old degraded-bank settings were optimal.
