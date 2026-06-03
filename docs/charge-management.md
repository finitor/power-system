# Charge Management

This system treats the battery BMS as the authority on the battery's safe operating envelope, and treats charge controllers as actuators that must stay inside that envelope. The supervisor is currently read-only for automatic control: it alerts on concerning conditions but does not autonomously rewrite charger limits.

## BMS Negotiation Signals

The Eco-Worthy rack batteries expose Pylon-style CAN telemetry. The most important charging constraints are:

| Signal | Meaning | Supervisor use |
|---|---|---|
| CVL | Charge voltage limit advertised by the BMS | Compare against Classic absorb, float, equalize, and max temp-comp voltage settings |
| CCL | Charge current limit advertised by the BMS | Compare against Classic battery current limit |
| Charge enable | Whether the BMS currently permits charge | Display and future alert/control input |
| Protections/alarms | BMS fault and warning state | Display as battery `Protection/Alarms`; any non-nominal value is significant |
| Min/max cell voltage | Cell-level top-of-charge behavior | Alert on high max-cell voltage and top-of-charge imbalance |
| Cell voltage delta | Difference between highest and lowest reported cell | Alert only near the top knee, where voltage delta is meaningful |

The BMS can change CCL dynamically. During the June 2, 2026 top-off observation, the BMS advertised 200 A through most of the charge, stepped down to 100 A near the top, and later stepped down to 40 A while max-cell voltage and cell delta rose. Actual battery charge current was far below these advertised limits, so the CCL reduction was interpreted as an advisory/taper signal rather than an immediate current constraint.

## Classic Settings Guard

Manual Classic writes should use `scripts/classic-charge-settings.py` instead of ad hoc Modbus snippets. The guarded writer reads the current BMS CVL/CCL before writing and refuses planned settings that exceed the advertised envelope unless `--force` is explicitly supplied.

The guard checks:

- Classic battery current limit must be less than or equal to BMS CCL.
- Classic absorb voltage must be less than or equal to BMS CVL.
- Classic float voltage must be less than or equal to BMS CVL.
- Classic equalize voltage must be less than or equal to BMS CVL.
- Classic max temp-comp voltage must be less than or equal to BMS CVL.

This is a write-time guard only. Because BMS CCL can fall later during taper, the supervisor also continuously reports read-only status conditions when the current Classic settings exceed the current BMS limits.

### Classic Modbus Write Modes

The Classic has two different practical write modes over Modbus TCP:

| Mode | Behavior | Use |
|---|---|---|
| Live / RAM-only | Changes take effect immediately but are lost when the Classic hard power-cycles | Short supervised experiments |
| Persisted / EEPROM | Changes take effect immediately and are saved across Classic hard power-cycles | New baseline settings |

The low-level Ethernet Modbus sequence is:

1. Open a TCP connection to the Classic.
2. Read the Classic serial number from registers `28673` and `28674`.
3. Unlock Ethernet writes by writing those two serial-number words to registers `20492` and `20493`.
4. Write the desired charge-setting registers, such as:
   - `4148`: battery output current limit, scaled by 10.
   - `4149`: absorb voltage, scaled by 10.
   - `4150`: float voltage, scaled by 10.
   - `4151`: equalize voltage, scaled by 10.
   - `4154`: absorb time in seconds.
   - `4155`: maximum temperature-compensated charge voltage, scaled by 10.
5. For a live / RAM-only change, stop here and close the TCP connection.
6. For a persisted / EEPROM change, write `0x0004` to register `4160` to set `ForceEEpromUpdateWriteF`.
7. Read back the charge settings and check that the Classic info flags do not include the EEPROM error bit `0x00000002`.

`scripts/classic-charge-settings.py` implements this sequence. By default it persists changes to EEPROM:

```sh
python scripts/classic-charge-settings.py \
  --classic-host 192.168.0.10 \
  --battery-current-limit 80.0 \
  --absorb-voltage 55.6 \
  --float-voltage 55.0 \
  --equalize-voltage 55.6 \
  --absorb-time 1950 \
  --max-temp-comp-voltage 55.6
```

For a temporary live-only experiment, add `--no-persist`:

```sh
python scripts/classic-charge-settings.py \
  --classic-host 192.168.0.10 \
  --absorb-time 1950 \
  --no-persist
```

Use `--dry-run` to print the planned settings and BMS guard result without writing anything. Use `--force` only when deliberately overriding a CVL/CCL guard refusal.

## Supervisor Status Conditions

The supervisor reports status conditions but does not autonomously act on them. Any active status condition makes the top display status `ERROR`, appears in terminal and web displays, and is logged in SQLite as `supervisor.status_condition`.

Current charge-management conditions:

| Condition | Trigger | Rationale |
|---|---|---|
| `Charge controller 0 CCL exceeds battery CCL` | Classic current limit is greater than BMS CCL | The charger setting is outside the BMS-advertised current envelope |
| `Charge controller 0 CVS exceeds battery CVL` | Any Classic charge voltage setpoint is greater than BMS CVL | The charger setting is outside the BMS-advertised voltage envelope |
| `Battery cell high` | Max cell >= 3.550 V for two consecutive samples | Warn before approaching common LiFePO4 overvoltage territory |
| `Battery cell overvoltage risk` | Max cell >= 3.600 V immediately | High-cell voltage is close enough to typical BMS protection thresholds to require intervention |
| `Battery cell delta high` | Delta >= 75 mV for two consecutive samples while max cell >= 3.450 V | Top-of-charge imbalance is becoming meaningful near the LiFePO4 voltage knee |
| `Battery cell delta critical` | Delta >= 100 mV for two consecutive samples while max cell >= 3.450 V | Imbalance is large enough near the top knee that continuing a top-off attempt is not useful |

Two consecutive samples are required for the warning-style thresholds to avoid false alarms from one noisy CAN read. The overvoltage-risk threshold is immediate because the consequence of waiting is worse than a false positive.

## Threshold Theory

LiFePO4 cell voltage is relatively flat through the middle of SOC and rises sharply near the top. Because of that, cell voltage delta is state-dependent:

- At mid-SOC, a moderate voltage delta can be noisy or not very meaningful.
- Near the top knee, a rising delta can mean one cell is accepting charge faster than the others or has reached the steep part of the curve earlier.
- Passive balancing is slow. If the charger keeps holding a high pack voltage, the high cell can continue rising while lower cells lag.

The supervisor therefore gates delta alerts on max-cell voltage. Delta is watched more closely only when max cell is at or above 3.450 V.

The initial thresholds are intentionally conservative and based on observed behavior:

- On June 2, 2026, max cell reached 3.513 V and delta peaked around 68 mV during a supervised elevated-voltage top-off attempt.
- The BMS reported no protections or alarms, and charge enable remained true.
- CCL tapered downward as max-cell voltage and delta rose.
- Rollback reduced charger output and the delta began falling.

The selected warning threshold of 75 mV is just above the observed peak, so normal repeats of this experiment should not alert unless imbalance grows beyond what has already been observed. The 100 mV threshold is a stronger signal that a top-off attempt should stop. The 3.550 V and 3.600 V max-cell thresholds are below typical LiFePO4 hard overvoltage values, leaving margin for manual intervention.

## Current Control Boundary

The supervisor does not automatically change Classic settings in response to these conditions. The current response policy is:

1. Alert visibly.
2. Let the operator decide whether to roll back charge settings, stop a top-off attempt, or continue observing.
3. Keep all automatic write behavior out of the supervisor until the manual policy has more history.

Possible future automation should start with conservative actions such as restoring the documented baseline settings or reducing Classic current limit, and should still require explicit enablement during early operation.
