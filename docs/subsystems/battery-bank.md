# Battery Bank

## Hardware

| Item | Value |
|---|---|
| Batteries | 2x Eco-Worthy Cubix 100 rack-mount batteries |
| Nominal voltage | 48 V |
| Capacity | 100 Ah each, 200 Ah total nominal |
| Chemistry | LiFePO4 |
| BMS | Built in, 100 A BMS per battery |
| Remote monitor | Eco-Worthy ESM-100 |
| Physical arrangement | Rack-mounted, details TBD |
| Candidate Pi interface | DSD TECH SH-C31G isolated USB-to-CAN adapter |

## Telemetry Goals

| Measurement | Source | Priority | Notes |
|---|---|---|---|
| Bank voltage | BMS / ESM-100, inverter, or charge controller | High | Prefer BMS/ESM-100 as first canonical value, with per-device comparison |
| Bank current | BMS / ESM-100 | High | Positive for charging, negative for discharging |
| State of charge | BMS / ESM-100 | High | ESM-100 reports SOC; confirm whether Pi can read the same value over CAN/RS485 |
| Battery temperature | BMS or external sensors | High | Track per-battery if possible |
| Per-battery voltage | BMS | Medium | Useful for imbalance detection |
| BMS alarms/faults | BMS | High | Needs exact interface research |
| Charge/discharge limits | BMS/manual specs | Medium | Important for control policy and alarms |

## Candidate Communication Path

Eco-Worthy documentation says the Cubix 100 supports Bluetooth/Wi-Fi app monitoring, RS485, CAN, and RS232. The current candidate for Pi-side CAN access is the DSD TECH SH-C31G isolated USB-to-CAN adapter.

This is a reasonable adapter choice for CAN experimentation because it is USB-attached, isolated, cabled with USB Type-B instead of a tight USB dongle, based on CANable 2.0, supports CAN 2.0A/B up to 1 Mbps, and is described by DSD TECH as SocketCAN-compatible.

The battery protocol table shows the CAN port as a host-computer/inverter port. It gives the CAN RJ45 pinout as pin 4 = CANH1, pin 5 = CANL1, pins 3 and 6 = GND, with pins 1, 2, 7, and 8 not connected. The default listed CAN protocol is Pylon CAN bus protocol V2.0.6_220510.

Validation needed before treating CAN as the production battery telemetry path:

- Confirm the physical CAN RJ45 pinout against the actual battery/manual revision.
- Confirm CAN termination and baud rate.
- Confirm whether Eco-Worthy publishes useful BMS telemetry over CAN, not only inverter-facing summary messages.
- Confirm the protocol selected in the Eco-Worthy app/tool, starting with the default Pylon CAN protocol.
- Compare CAN data against the Eco-Worthy app and/or RS485 host software.

The Pi software defaults to `BATTERY_CAN_PROTOCOL=pylon`. A later change to the Eco-Worthy app's "Victron" protocol can use `BATTERY_CAN_PROTOCOL=ecoworthy-victron` or `offgrid-supervisor --battery-can-protocol ecoworthy-victron`. The May 31, 2026 battery-only capture of that app setting still used 500 kbit/s standard CAN frames and preserved the core display metrics: SOC/SOH, pack voltage/current/temperature, charge limits, cell voltage range, cell temperature range, and installed capacity. It did not look like VE.Can/NMEA2000 at 250 kbit/s.

Keep RS485 and RS232 as fallback or parallel research paths because the protocol table also shows host-computer-capable RS485 and RS232 options. Preferred RS485 fallback hardware is an isolated USB-to-RS485 adapter, such as the Waveshare USB TO RS485/422 industrial isolated converter.

## Shunt Decision

The Eco-Worthy ESM-100 remote monitor changes the battery monitor decision. It already displays battery voltage, current, and state of charge, so a separate WhizBang Jr shunt is not required for the first battery telemetry build if the Pi can obtain equivalent BMS data over CAN or RS485.

Keep the WhizBang Jr / external shunt as optional, and likely avoid it if AUX2 becomes important for Classic control:

- Independent cross-check against BMS-reported current and SOC.
- Midnite Classic integration if WhizBang-derived net battery current is useful enough for end-amps absorb termination logic to justify consuming AUX2.
- Possible canonical system-level current source if BMS telemetry proves incomplete or inaccessible.

Reasons to defer the shunt:

- Adds another high-current measurement point and wiring change.
- Duplicates values already visible on the ESM-100 for Pi-level battery monitoring.
- Consumes Classic AUX2, which may be more useful for charge inhibit or other high-level Classic control.
- Does not remove the need to read BMS alarms, charge-allowed state, low-temperature inhibit, and per-battery details.

## Control Boundaries

The Raspberry Pi should not be the primary battery safety device. Battery protection remains the responsibility of the BMS, fuses, breakers, disconnects, and any required external protection hardware.

The bank is LiFePO4, so supervisory charge policy should avoid sustained lead-acid-style float. The battery BMS remains the final protection layer, but normal operation should not depend on the BMS repeatedly blocking charge because legacy chargers are holding the bank too high for too long.

Avoid designs that depend on the internal battery BMS opening while chargers are active. If a BMS opens under charge, the remaining DC bus can see a sharp transient because the charger has suddenly lost the battery as its voltage reference and energy sink. The supervisor should use charge-allowed telemetry and conservative thresholds to stop chargers before the BMS must protect the cells.

Possible future supervisory actions:

- Alert on low SOC, high/low voltage, high temperature, or BMS fault.
- Request load shedding through documented auxiliary contactors.
- Inhibit non-critical loads when battery reserve is low.
- Publish charge-allowed, discharge-allowed, full-charge, and recharge-needed states for charger supervisors.
- Alert if bank voltage remains near the top of charge for longer than the configured LiFePO4 hold-time limit.
- Alert if either battery reports charge disconnect, overvoltage, low-temperature charge inhibit, or disappears from telemetry during active charging.

Do not implement any battery-related control until the disconnects, manual override path, and fail-safe state are documented.

## Wiring And Protection

Document:

- Parallel wiring method for the two batteries.
- Per-battery overcurrent protection.
- Main DC disconnect.
- Cable gauge and length.
- Torque specs, if available.
- Grounding/bonding arrangement.

## Open Questions

- What exact chemistry and BMS model are used in the Cubix 100?
- Can each battery provide telemetry independently?
- Is a separate WhizBang Jr / shunt monitor useful enough to consume Classic AUX2, or should AUX2 be preserved for charger control?
- What are the manufacturer limits for parallel operation?
- Does CAN expose full telemetry, or only inverter-oriented battery summary/status?
- Is RS485 a better first integration path for Pi-based monitoring?
- What voltage/SOC thresholds should define full-charge, charge stop, and charge restart for this exact battery model?
- Does the BMS expose a charge-allowed or charge-disconnect warning before opening its internal MOSFETs/contactors?
