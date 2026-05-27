# Battery Temperature Control

## Goal

Keep the Eco-Worthy Cubix 100 battery bank inside a safe temperature window, with special attention to winter charging. This subsystem is about battery temperature management, not whole-array diversion.

Winter lockout is acceptable if it protects the batteries. The main downside is loss of telemetry, not loss of critical occupied-house service.

Core goals:

- Prevent charging when the batteries are too cold.
- Warm the insulated battery compartment when energy is available and safe to use.
- Avoid heat buildup in warm weather.
- Fail heater control off if the Raspberry Pi dies.
- Keep the Pi supervisory, not safety-critical.

## Solar Context

Canadian Solar CS6X-300P panel specs from the local datasheet:

| Parameter | Per panel | 4s x 2p array estimate |
|---|---:|---:|
| Pmax | 300 W | 2400 W |
| Vmp | 36.1 V | 144.4 V |
| Imp | 8.30 A | 16.6 A |
| Voc | 44.6 V | 178.4 V at 25 C |
| Isc | 8.87 A | 17.74 A |

Cold weather raises PV open-circuit voltage. With the CS6X Voc temperature coefficient of -0.34%/C, a 4-panel series string can exceed 200 V in very cold weather. Confirm actual array configuration and Classic 200 cold-weather limits before changing PV wiring or adding any PV-side control.

## Enclosure Strategy

Use an insulated but ventilatable battery compartment. Do not build a permanently sealed foam box.

Recommended insulation approach:

- Rigid closed-cell foam board, preferably foil-faced polyiso.
- About 2 inches where space allows, 1 inch where tight.
- Removable panels or a removable winter jacket.
- Foil-taped seams where appropriate.
- A deliberate vent/fan path for warm weather.
- Non-combustible local backing near heaters, such as aluminum sheet, sheet metal, cement board, or mineral wool.

Operational modes:

| Mode | Behavior |
|---|---|
| Winter | Vents closed, heater enabled by thermostat/Pi permissive, charge inhibited when too cold |
| Warm weather | Vents open or fan enabled, heater locked out, high-temp alarms active |
| Fault | Heater hard-off, optional fan/vent on, charging inhibited or derated if battery temperature is high |

## Temperature Sensors

Use DS18B20 1-Wire sensors for cheap multi-point temperature monitoring.

Recommended sensor locations:

- Battery/rack area.
- Enclosure air.
- Heater spreader plate.
- Outside ambient.

All DS18B20 sensors can share one Raspberry Pi GPIO line. Each sensor has a unique 64-bit address, so installation must include a mapping from sensor ID to physical location.

Typical wiring:

```text
Pi 3.3 V -> DS18B20 VDD
Pi GND   -> DS18B20 GND
Pi GPIO4 -> DS18B20 DATA

4.7k pull-up resistor between DATA and 3.3 V
```

Use the ASAIR AM2302/DHT22 separately for utility-room ambient humidity and general room temperature. It is useful because the utility room contains water equipment, but it should not be used as the sole heater safety cutoff or charge-temperature permissive sensor.

## Heater Candidate

Current leading candidate:

| Candidate | Notes |
|---|---|
| AliExpress 1-set V19 fused 48 V / 51.2 V silicone heating pad pair | Pair is wired in series to make a 48 V / 200 W heater assembly; about 4.2 A at 48 V; reasonable prototype candidate; requires external thermostat/control |

The mat's included fuse is useful branch protection, but it is not a thermostat and does not replace the upstream heater-circuit breaker, thermal cutoff, or temperature controller.

Mounting guidance:

- Do not stick mats directly to Eco-Worthy rack battery cases unless Eco-Worthy approves it.
- Prefer mounting mats to an aluminum heat spreader plate or enclosure surface.
- Heat enclosure air/rack metalwork rather than one concentrated spot.
- Add a small low-temperature-rated circulation fan if the compartment has cold/hot pockets.

## Heater Power

At 48 V:

| Heater Power | Current | Approximate Resistance |
|---:|---:|---:|
| 100 W | 2.1 A | 23 ohm |
| 200 W | 4.2 A | 12 ohm |
| 300 W | 6.3 A | 7.7 ohm |
| 500 W | 10.4 A | 4.6 ohm |

The 200 W mat is a good first implementation. A rough thermal-mass estimate for two 100 Ah rack batteries is about 0.8-1.0 kWh to raise the battery mass from -40 C to +5 C before enclosure losses. At 200 W this could take roughly 4-6 hours in a good enclosure.

Do not size this subsystem to absorb the full 2400 W array. That is water-heater/diversion-load territory, not battery temperature control.

If future summer water heating is desired, design it as a separate high-current circuit. Do not oversize the battery-heater branch to become a water-heater branch.

## Control Behavior

Draft thresholds:

| Condition | Action |
|---|---|
| Battery/enclosure below 0 C | Inhibit or avoid charging; allow heat if energy source/SOC permits |
| Below 3 C | Thermostat calls for heat |
| Above 6-10 C | Thermostat stops heat; charging may resume if BMS agrees |
| Above 35 C | Heater locked out; warm-temperature warning |
| Above 40 C | High-temperature warning; investigate ventilation/charging |
| Above battery manufacturer limit | Inhibit or derate charging if possible |
| Any heater sensor fault | Heater off |
| BMS low-temperature charge fault | Keep Classic inhibited and heat only if safe |

Winter deadlock is possible if the batteries cold-lock and cannot discharge to power their own heater. This is acceptable if telemetry loss is the only consequence, but the system should recover cleanly when temperatures rise.

## Charge Inhibit

Do not depend on Classic AUX2 for charge inhibit until its best use is settled. WhizBang Jr would consume AUX2, but leaving WhizBang Jr out may preserve AUX2 for charge inhibit or other high-level Classic control functions.

Preferred path:

- Raspberry Pi talks to the Midnite Classic over Ethernet/Modbus TCP.
- Pi reads battery/BMS temperature and low-temperature charge status over CAN or RS485.
- Pi sets Classic Mode Off, or sets an appropriate charge-current limit, when batteries are too cold.
- Pi restores charging only after battery temperature has recovered and BMS status agrees.

Conservative fallback:

- DC-rated PV input contactor or shunt-trip breaker under hardware thermostat/BMS/Pi supervision.
- Use only if software control is unavailable or untrusted.
- Do not use AC-only relays or underspecified DC relays on PV strings.

## Heater Power Path

Use normally-open / fail-off logic. The Pi must actively permit heat; if the Pi dies, the heater turns off.

```text
48 V bus
  -> 10 A DC breaker/manual disconnect, 100-150 VDC preferred
  -> DC SSR or MOSFET switch
  -> one-shot thermal fuse near heater
  -> fused 48 V / 200 W mat pair
  -> 48 V return
```

Manual disconnect recommendation:

- Use a DC-rated breaker as both branch protection and service disconnect.
- For 200 W heater: 10 A, 100-150 VDC preferred.
- MidNite Solar MNEPV10 or similar DC breaker is a good class of part.

## Control Path

The thermostat and Pi should switch the SSR control input, not the heater current.

Recommended fail-off chain:

```text
12 V control rail
  -> thermostat/controller heat-call output
  -> normally-closed 60 C heater over-temp switch
  -> Pi-controlled normally-open permissive
  -> SSR input
  -> control return
```

Heater turns on only when all are true:

- Thermostat calls for heat.
- Resettable over-temp switch is closed.
- Pi actively permits heat.
- SSR input is energized.

## Thermal Cutoffs

Use two independent over-temperature layers:

| Device | Suggested Rating | Placement | Purpose |
|---|---:|---|---|
| Resettable NC thermal switch | 60 C | Heater spreader plate | Opens control path if heater area gets too hot |
| One-shot thermal fuse | 72-84 C | Near heater mat | Final backup if SSR/control fails on |

Do not rely on the Pi as the only over-temperature protection. Also do not rely on the mat's included fuse as a thermal safety device.

## Pi Control Interface

Candidate Pi-side control interface:

| Candidate | Role | Notes |
|---|---|---|
| 8-channel PC817 optocoupler isolation board, 3.3-30 V signal adapter | GPIO isolation / level shifting | Maximum output current is about 10 mA/channel; suitable for SSR inputs only if SSR input current is below that, otherwise add a transistor/MOSFET driver |

Use the optocoupler board as a signal layer:

```text
Pi GPIO
  -> optocoupler input
  -> isolated output using 5 V or 12 V control rail
  -> optional transistor/MOSFET driver
  -> thermostat / over-temp interlock chain
  -> DC SSR input
```

Do not use the optocoupler board to switch the 48 V heater load directly.

## DC Switching Candidates

Candidate DC SSR/MOSFET switches:

| Candidate | Rating | Control input | Notes |
|---|---:|---|---|
| Sensata/Crydom EL100D10-12 | 10 A, 3-100 VDC | 10-14 VDC | Preferred 12 V-control candidate for 48 V / 200 W heater; input current is around 10 mA minimum, so use a driver if the opto board is limited to 10 mA |
| Sensata/Crydom EL100D20-12 | 20 A, 3-100 VDC | 10-14 VDC | More current headroom for future modest DC loads; still use heatsinking/derating |
| Sensata/Crydom GN 84134860 | 15 A, 100 VDC | 3.5-32 VDC | Good headroom for 48 V heater loads |
| Sensata/Crydom GN 84134850 | 10 A, 200 VDC | 3.5-32 VDC | More voltage headroom |
| Generic SSR-10DD / SSR-25DD | 10-25 A, often 5-60 VDC | 3-32 VDC | Bench-only unless source is trusted; many listings exaggerate ratings |

Avoid:

- AC-output SSRs such as `SSR-25DA`.
- Listings that say SCR, triac, or zero-cross for DC heater switching.
- Mystery SSRs without a real datasheet, derating, and output topology.
- 60 VDC output SSRs for final install if better 100 VDC parts are available; the 48 V LiFePO4 bank can reach about 58.4 V while charging.

## Ventilation / Cooling

The insulated compartment can become a heat trap in warm weather.

Recommended approach:

- Use the dual-setpoint thermostat/controller for heat and cooling calls.
- Use a 12 V fan with a normally-closed gravity/spring backdraft damper, or a small motorized 12 V damper.
- A separate fan + damper is more practical than finding a small integrated fan/actuator assembly.
- Keep the vent closed in winter and open/fan-assisted in warm conditions.

Avoid permanently open vents in winter.

## Raspberry Pi Responsibilities

- Monitor BMS temperature/status over CAN or RS485.
- Monitor enclosure/battery/heater temperatures over DS18B20.
- Monitor heater command and heater state.
- Permit heater operation only when policy allows.
- Inhibit Classic charging over Modbus when batteries are too cold.
- Log heater runtime and temperature response.
- Alert on sensor faults, heating failures, high temperature, or loss of telemetry.

The Raspberry Pi is supervisory. The thermostat, thermal switch, thermal fuse, fuse/breaker, and BMS must still make the system safe when the Pi is offline.

## Open Questions

- What is the Eco-Worthy Cubix 100 low-temperature charge cutoff and recovery temperature?
- Does the BMS expose low-temperature charge inhibit over CAN or RS485?
- Can the Raspberry Pi reliably set Classic Mode Off/On or current limit through Modbus?
- Is AUX2 already committed to another Classic function?
- What are the final enclosure dimensions, insulation thickness, and leakage paths?
- Is there generator/AC input available for manual recovery heat?
- Which thermostat/controller model will own heat/cool setpoints?
- Which DC SSR and thermal cutoff parts will be selected?
