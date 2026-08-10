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
| 48 V 200 W ceramic heater | About 4.2 A at 48 V; selected substitute for the previously considered AliExpress V19 fused silicone heating pad pair; requires external thermostat/control |

Any heater-integrated fuse or thermal limiter is useful only as local backup protection. It does not replace the upstream heater-circuit breaker, thermal cutoff, or temperature controller.

Mounting guidance:

- Do not direct-mount heaters to Eco-Worthy rack battery cases unless Eco-Worthy approves it.
- Prefer mounting the heater to an aluminum heat spreader plate or enclosure surface.
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

The 200 W heater is a good first implementation. A rough thermal-mass estimate for two 100 Ah rack batteries is about 0.8-1.0 kWh to raise the battery mass from -40 C to +5 C before enclosure losses. At 200 W this could take roughly 4-6 hours in a good enclosure.

Do not size this subsystem to absorb the full 2400 W array. That is water-heater/diversion-load territory, not battery temperature control.

If future summer water heating is desired, design it as a separate high-current circuit. Do not oversize the battery-heater branch to become a water-heater branch.

## Control Behavior

The Pi supervisor (`RelaySupervisor` in `relay_control.py`) evaluates relay states every tick.

### Heater + Fan (relay CH1 — heat_fan)

Hysteresis control on pack temperature and temperature-normalized Classic VOC
(as an irradiance proxy):

| Condition | Action |
|---|---|
| Pack temp < 2 °C **and** normalized Classic VOC ≥ 158 V continuously for 60 s | Activate relay (heater + fan on) |
| Pack temp > 5 °C **or** normalized Classic VOC < 154 V | Deactivate relay (heater + fan off) |
| Battery, Classic, or local ambient-temperature telemetry unavailable | Reactive heat fails off |

Normalize measured VOC to a 25 °C reference using the modules'
−0.34%/°C VOC coefficient:

```text
normalized_VOC = measured_VOC / (1 + 0.0034 × (25 − local_ambient_temperature_C))
```

The correction matters because cold modules raise VOC even in weak winter
light. Without it, a fixed threshold calibrated in summer can falsely indicate
strong solar input. The 158/154 V normalized hysteresis and 60-second
qualification period prevent chatter. At 19.3 °C ambient the equivalent raw
thresholds are about 161.1/157.0 V; at 0 °C they are about 171.4/167.1 V; at
−20 °C they are about 182.2/177.6 V.

The cut-in was derived from post-2026-07-18 telemetry after array 0 was
corrected from the faulted 4s∥3s wiring to 4s2p. Using the local ambient probe
for normalization, ≥ 158 V coincided with at least 200 W of Classic output in
79.1% of Bulk samples and at least 400 W in 56.4%; average output was 639 W.
Two 2026-08-10 live checks qualify: 166.8 V VOC / 19.3 °C ambient normalized
to 163.6 V at about 1.6 kW, and 161.8 V / 19.4 °C normalized to 158.8 V at
862 W. The older 132/130 V gate was established while the array was miswired
and is not valid for the corrected topology.

Cut-in at 2 °C rather than 0 °C provides headroom above the BMS
low-temperature charge cutout. Compensation uses the existing local ambient
probe, so cold-lock recovery has no internet or external-weather dependency.

The fan runs in tandem with the heater to circulate warm air through the battery compartment. Both are on the same relay contact.

### Charge Disable (relay CH2 — charge_disable)

Activates the Classic AUX1 hardware charge inhibit whenever the supervisor commands Classic to 0 A, via any path:

- CCL allocator hard-disables Classic (`disable=True`)
- CCL allocator or manual ceiling override produces a 0 A effective target
- Manual `/api/v1/control/allocation/manual-limit` sets Classic ceiling to 0 A

The relay provides a hardware-level backstop that mirrors the software limit without depending on Modbus write success.

Winter deadlock is possible if the batteries cold-lock and cannot discharge to power their own heater. This is acceptable if telemetry loss is the only consequence, but the system should recover cleanly when temperatures rise.

## Charge Inhibit

Classic AUX2 is used as the hardware charge-disable line, driven by relay CH2. When the relay closes, 12 V is applied to AUX2+ (threshold >6 V), forcing the Classic to Resting.

**TODO:** Remove WhizBang Jr wiring from Classic AUX2 terminals. WhizBang Jr is currently on AUX2 and must be physically cleared before AUX2 can be used as the charge-disable input. The Eco-Worthy BMS/ESM-100 over CAN makes WhizBang Jr redundant.

**TODO:** Reconfigure Classic AUX2 from WhizBang Jr (register 4165 = 0x5201) to "Active HIGH (input) turn off" (function value 15, target register value 0x4F01). Write via the existing `unlock_ethernet_writes` + `write_register` path and force EEprom save. Confirm persistence across a Classic power cycle.

**TODO:** Wire relay CH2: COM → 12 V supply (GND shared with Classic); NO → Classic AUX2+ terminal; Classic AUX2− → GND.

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
  -> 48 V / 200 W ceramic heater
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
| One-shot thermal fuse | 72-84 C | Near heater | Final backup if SSR/control fails on |

Do not rely on the Pi as the only over-temperature protection. Also do not rely on any heater-integrated limiter as the only thermal safety device.

## Pi Control Interface

The Pi drives a **Javino 2-channel optocoupler-isolated relay module** (SRD-05VDC-SL-C relays, on-hand) directly from GPIO. Both jumpers S1/S2 are set to high-level trigger so relays fail off if the Pi resets or GPIO floats low.

| Pi pin | GPIO (BCM) | Relay channel | Function |
|---|---|---|---|
| Pin 2 | 5 V power | — | Relay board VCC |
| Pin 6 | GND | — | Relay board GND (common with 12 V supply GND) |
| Pin 11 | GPIO 17 | CH1 | heat_fan — heater SSR control + 12 V fan |
| Pin 13 | GPIO 27 | CH2 | charge_disable — Classic AUX2 charge inhibit (>6 V on AUX2+) |

GPIO pins are configurable via `RELAY_HEAT_FAN_GPIO` and `RELAY_CHARGE_DISABLE_GPIO` env vars.

**TODO:** Wire relay CH1 NO contacts → 12 V bus → SSR MRD-060D10 control+ (pin 3) and fan+ in parallel; SSR control− and fan− to common GND. The relay contacts switch the 12 V control supply into the SSR input; the relay board's 5 V VCC is separate from the switched 12 V load.

**TODO:** Wire relay CH2: COM → 12 V supply (GND shared with Classic); NO → Classic AUX2+ terminal; Classic AUX2− → GND.

## DC Switching — Selected Parts

The **MRD-060D10** DC solid-state relay is on hand and selected for bench and initial install use.

| Parameter | Value |
|---|---|
| Output (load side) | 1–60 VDC, 10 A |
| Control input | 4–32 VDC, 12 mA |
| Control path | 12 V bus via relay CH1 contacts (4.2 A heater load, well within rating) |

The 12 V relay contact switches the SSR control input. The SSR in turn switches the 48 V heater load. Verify heatsinking before sustained use at 48 V / 4.2 A.

Voltage margin note: 48 V LiFePO4 can reach ~58.4 V charging. MRD-060D10 is rated 60 VDC class — acceptable for initial use but monitor. Higher-margin alternatives (100+ VDC class) preferred for permanent install.

**TODO:** Validate MRD-060D10 heatsinking requirements at 4.2 A continuous. Mount on heatsink or chassis if needed before sustained heater use.

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
