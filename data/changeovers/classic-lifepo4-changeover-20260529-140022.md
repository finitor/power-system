# Classic LiFePO4 Changeover 20260529-140022

## Before Snapshot

| Register | Setting | Raw | Decoded |
|---:|---|---:|---:|
| 4148 | Battery current limit | 400 | 40.0 A |
| 4149 | Absorb voltage | 592 | 59.2 V |
| 4150 | Float voltage | 540 | 54.0 V |
| 4151 | Equalize voltage | 648 | 64.8 V |
| 4152 | Sliding current limit | 400 | 400 A |
| 4153 | Register 4153 | 0 | 0 |
| 4154 | Absorb time | 7200 | 7200 s |
| 4155 | Max temp-comp voltage | 648 | 64.8 V |
| 4156 | Min temp-comp voltage | 528 | 52.8 V |
| 4157 | Temp comp value | 50 | -5.0 mV/C/cell |
| 4158 | Register 4158 | 0 | 0 |
| 4159 | Register 4159 | 0 | 0 |
| 4160 | Register 4160 | 0 | 0 |
| 4161 | Register 4161 | 0 | 0 |
| 4162 | Equalize time | 3600 | 3600 s |
| 4163 | Equalize interval | 30 | 30 days |
| 4164 | MPPT mode | 11 | 0x000B |
| 4165 | AUX function word | 20993 | 0x5201 |

## Writes

| Register | Setting | Old raw | New raw | Requested decoded | Readback | OK |
|---:|---|---:|---:|---:|---:|---|
| 4149 | Absorb voltage | 592 | 552 | 55.2 V | 552 | True |
| 4151 | Equalize voltage | 648 | 552 | 55.2 V | 552 | True |
| 4150 | Float voltage | 540 | 540 | 54.0 V | 540 | True |
| 4154 | Absorb time | 7200 | 300 | 300 s | 300 | True |
| 4148 | Battery current limit | 400 | 800 | 80.0 A | 800 | True |
| 4162 | Equalize time | 3600 | 0 | 0 s | 0 | True |
| 4163 | Equalize interval | 30 | 0 | manual/disabled auto EQ | 0 | True |
| 4155 | Max temp-comp voltage | 648 | 552 | 55.2 V | 552 | True |
| 4157 | Temp comp value | 50 | 1 | -0.1 mV/C/cell | 1 | True |

## After Snapshot

| Register | Setting | Raw | Decoded |
|---:|---|---:|---:|
| 4148 | Battery current limit | 800 | 80.0 A |
| 4149 | Absorb voltage | 552 | 55.2 V |
| 4150 | Float voltage | 540 | 54.0 V |
| 4151 | Equalize voltage | 552 | 55.2 V |
| 4152 | Sliding current limit | 400 | 400 A |
| 4153 | Register 4153 | 0 | 0 |
| 4154 | Absorb time | 300 | 300 s |
| 4155 | Max temp-comp voltage | 552 | 55.2 V |
| 4156 | Min temp-comp voltage | 528 | 52.8 V |
| 4157 | Temp comp value | 50 | -5.0 mV/C/cell |
| 4158 | Register 4158 | 0 | 0 |
| 4159 | Register 4159 | 0 | 0 |
| 4160 | Register 4160 | 0 | 0 |
| 4161 | Register 4161 | 0 | 0 |
| 4162 | Equalize time | 0 | 0 s |
| 4163 | Equalize interval | 0 | 0 days |
| 4164 | MPPT mode | 11 | 0x000B |
| 4165 | AUX function word | 20993 | 0x5201 |

## Rollback Raw Values

Use only if the old lead-acid bank is physically reinstalled.

| Register | Setting | Rollback raw |
|---:|---|---:|
| 4149 | Absorb voltage | 592 |
| 4151 | Equalize voltage | 648 |
| 4150 | Float voltage | 540 |
| 4154 | Absorb time | 7200 |
| 4148 | Battery current limit | 400 |
| 4162 | Equalize time | 3600 |
| 4163 | Equalize interval | 30 |
| 4155 | Max temp-comp voltage | 648 |
| 4157 | Temp comp value | 50 |
