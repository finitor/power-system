# Charge Management

This system treats the battery BMS as the authority on the battery's safe operating envelope, and treats charge controllers as actuators that must stay inside that envelope. The supervisor can automatically allocate charge-controller current limits when charge allocation is enabled; otherwise it reports telemetry and write-time guard failures.

## BMS Negotiation Signals

The Eco-Worthy rack batteries expose Pylon-style CAN telemetry. The most important charging constraints are:

| Signal | Meaning | Supervisor use |
|---|---|---|
| CVL | Charge voltage limit advertised by the BMS | Compare against Classic absorb, float, equalize, and max temp-comp voltage settings |
| CCL | Charge current limit advertised by the BMS | Input to the charge allocator's closed-loop current budget |
| Charge enable | Whether the BMS currently permits charge | Display and future alert/control input |
| Protections/alarms | BMS fault and warning state | Display as battery `Protection/Alarms`; any non-nominal value is significant |
| Min/max cell voltage | Cell-level top-of-charge behavior | Guardrail input to the charge allocator |
| Cell voltage delta | Difference between highest and lowest reported cell | Guardrail input to the charge allocator near the top knee |

The BMS can change CCL dynamically. During the June 2, 2026 top-off observation, the BMS advertised 200 A through most of the charge, stepped down to 100 A near the top, and later stepped down to 40 A while max-cell voltage and cell delta rose. Actual battery charge current was far below these advertised limits, so the CCL reduction was interpreted as an advisory/taper signal rather than an immediate current constraint.

## Classic Settings Guard

Manual Classic writes should use `scripts/classic-charge-settings.py` instead of ad hoc Modbus snippets. The guarded writer reads the current BMS CVL/CCL before writing and refuses planned settings that exceed the advertised envelope unless `--force` is explicitly supplied.

The guard checks:

- Classic battery current limit must be less than or equal to BMS CCL.
- Classic absorb voltage must be less than or equal to BMS CVL.
- Classic float voltage must be less than or equal to BMS CVL.
- Classic equalize voltage must be less than or equal to BMS CVL.
- Classic max temp-comp voltage must be less than or equal to BMS CVL.

This is a write-time guard only. Because BMS CCL can fall later during taper, the charge allocator continuously adjusts controller current limits to stay inside the current BMS envelope. The supervisor no longer raises a passive warning simply because a controller's configured current limit is higher than the instantaneous BMS CCL.

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

Current charge-management condition:

| Condition | Trigger | Rationale |
|---|---|---|
| `Charge controller 0 CVS exceeds battery CVL` | Any Classic charge voltage setpoint is greater than BMS CVL | The charger setting is outside the BMS-advertised voltage envelope |

Current-limit exceedance, high-cell voltage, and high cell delta are handled by the allocator's closed-loop budget resolver instead of by passive supervisor warning/error conditions.

## Threshold Theory

LiFePO4 cell voltage is relatively flat through the middle of SOC and rises sharply near the top. Because of that, cell voltage delta is state-dependent:

- At mid-SOC, a moderate voltage delta can be noisy or not very meaningful.
- Near the top knee, a rising delta can mean one cell is accepting charge faster than the others or has reached the steep part of the curve earlier.
- Passive balancing is slow. If the charger keeps holding a high pack voltage, the high cell can continue rising while lower cells lag.

The allocator therefore gates delta guardrails on max-cell voltage. Delta is watched more closely only when max cell is in the configured upper-cell zone.

The initial thresholds are based on observed behavior and were later moved from passive alerts into allocator guardrails:

- On June 2, 2026, max cell reached 3.513 V and delta peaked around 68 mV during a supervised elevated-voltage top-off attempt.
- The BMS reported no protections or alarms, and charge enable remained true.
- CCL tapered downward as max-cell voltage and delta rose.
- Rollback reduced charger output and the delta began falling.

The current allocator guardrails are documented in
[`charge-current-allocation.md`](charge-current-allocation.md). As of
2026-06-18, they are max-cell stop at 3.62 V, recovery / upper-zone threshold at
3.55 V, and cell-delta stop at 150 mV in that upper zone.

## Current Control Boundary

The supervisor status layer still reports voltage setpoint violations against
BMS CVL because those are static configuration mismatches. Dynamic current and
cell-voltage conditions are handled in the allocator loop instead:

1. `ChargeCeiling` resolves a net charge allowance from BMS CCL, charge-enable,
   cell guardrails, and low-temperature charge protection.
2. `ChargeCurrentAllocator` distributes that allowance across the controllers.
3. The live supervisor writes the resulting controller current limits and EPEver
   coil state when `--charge-allocation` is enabled.
