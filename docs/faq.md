# FAQ

## Why can the Classic be in Float while producing high power?

`Float` is a voltage-regulation stage, not a fixed low-power mode.

The MidNite Classic does not know the Eco-Worthy BMS state of charge directly. It decides charge stage from its own voltage, timer, and controller state logic. Once it reaches the configured absorb target and satisfies the absorb timer, it can move to Float even if the BMS SOC is not 100%.

In Float, the Classic tries to hold the battery bus near the configured float voltage. If the house loads plus the battery bank are willing to consume 1500 W at that voltage, the Classic can produce about 1500 W and still correctly report `Float`. If the battery bank is near the upper voltage knee and house loads are smaller, the same Float stage may only need a few hundred watts.

Observed example from May 31, 2026:

| Value | Reading |
|---|---:|
| Classic stage | Float |
| Classic state | MPPT or regulating voltage |
| Classic battery voltage | 54.8 V |
| Classic output current | 10.5 A |
| Classic output power | 578 W |
| PV voltage | 129.2 V |
| Battery-bank CAN voltage | 54.52 V |
| Battery-bank CAN charge current | 6.8 A |
| Battery-bank CAN SOC | 88% |
| Estimated household load | 207 W |

That snapshot looked like normal voltage regulation: the controller was holding the battery near Float voltage, the battery was accepting only modest current, and the rest of the Classic output was feeding house load.

## Is the BMS throttling solar output when Classic power falls in Float?

Usually not directly.

In the current Pylon-compatible CAN telemetry, the batteries advertise a charge-current limit of 200 A and report charge enabled. That is not a restrictive BMS command for the observed 10 A class charging. If the BMS were actively blocking charge, expect evidence such as charge disabled, alarms, abrupt current changes, or a much lower advertised charge-current limit.

The more likely explanation is battery acceptance under constant-voltage charging:

1. The Classic holds the DC bus near Float voltage.
2. The LiFePO4 cells are already at a high voltage for their SOC/condition.
3. The voltage difference pushing current into the cells is small.
4. Charge current naturally falls.
5. The Classic reduces PV draw because more power is not needed to maintain the voltage target.

The BMS is still part of the battery system and may protect the cells if a hard limit is approached, but normal tapering near the top of charge is not the same as a BMS disconnect or active throttle command.

## How does battery state limit charge acceptance electrically?

A battery is better modeled as a voltage source plus internal/effective resistance, not as a plain resistor. For a rough charging model:

```text
charge current = (charger voltage - battery open-circuit voltage) / effective resistance
```

At lower SOC, the battery's internal/open-circuit voltage is lower. With the Classic holding the bus at a fixed Float voltage, there is more voltage difference pushing current into the cells, so more current flows.

At higher SOC, the battery's internal voltage rises. The same charger voltage leaves less voltage headroom, so current falls.

Oversimplified example:

```text
Lower SOC:
54.0 V charger - 52.5 V battery = 1.5 V push

Higher SOC:
54.0 V charger - 53.8 V battery = 0.2 V push
```

For the same effective resistance, the second case accepts much less current. This can look like the battery is presenting a higher resistance, but much of the effect is the battery's own voltage rising toward the charger voltage.

LiFePO4 makes this feel unintuitive because voltage is relatively flat through the middle of the SOC range and then rises more sharply near the upper knee. A voltage-based charger can therefore enter Float based on its own rules while the BMS SOC still says something less than "full" in the everyday sense.
