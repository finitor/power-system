# Battery CAN Interface Research

## Current Candidate

DSD TECH SH-C31G isolated USB-to-CAN adapter.

## Selected Amazon Battery Comms BOM

| Item | Quantity | Use |
|---|---:|---|
| DSD TECH SH-C31G isolated USB-to-CAN adapter | 1 | Primary CAN test path from Raspberry Pi to Cubix battery CAN port |
| Waveshare USB TO RS485/422 industrial isolated converter | 1 | RS485 fallback path from Raspberry Pi to Cubix RS485-1 port |
| Poyiccot RJ45 8P8C female jack to screw terminal breakout | 2 | Adapter-side or bench breakouts for CAN and RS485 |
| Poyiccot 90 degree RJ45 male to screw terminal breakout | 1 | Experimentation and pinout probing |
| Bare Cat-6 cable and RJ45 male plugs | On hand | Final custom crimped comms cables |

## Preliminary Assessment

Status: solid candidate for CAN experimentation, with documented Cubix 100 CAN pinout and protocol choices. Still validate actual traffic before making it the production battery telemetry path.

Why it fits:

- The Raspberry Pi 3 Model B v1.2 has USB 2.0 ports, so a USB CAN adapter is mechanically and electrically simpler than adding a GPIO/SPI CAN HAT.
- DSD TECH describes the SH-C31G as based on CANable 2.0.
- It supports CAN 2.0A/B up to 1 Mbps.
- DSD TECH describes SocketCAN compatibility.
- It is an isolated version, which is preferable for a noisy 48 V power-system cabinet.
- It uses a USB Type-B cable instead of a USB-stick form factor, which should be easier to strain-relieve in a tight Raspberry Pi enclosure.
- Eco-Worthy documents CAN as one of the Cubix 100 communication methods.
- The Cubix 100 protocol table documents the CAN port as suitable for host computer/inverter connection.
- The protocol table shows default CAN support for the Pylon CAN bus protocol V2.0.6_220510.
- The SH-C31G is based on CANable 2.0, and CANable 2.0 includes switchable onboard 120 ohm CAN termination. Treat the SH-C31G as likely to have onboard switchable termination, but verify the actual board when it arrives.

## Cubix 100 CAN Port Notes

From the battery documentation protocol table:

| RJ45 Pin | Signal |
|---:|---|
| 1, 8 | NC |
| 2, 7 | NC |
| 4 | CANH1 |
| 5 | CANL1 |
| 3, 6 | GND |

Supported CAN protocol choices shown in the table include:

- Pylon CAN bus protocol V2.0.6_220510, default.
- Growatt BMS CAN-Bus protocol, low voltage.
- Goodwe CAN.
- Sofar CAN.
- Victron CAN.
- Luxpowertek battery CAN protocol.
- Deye low-voltage battery CAN.
- Ginlong low-voltage battery CAN protocol.
- SMA CAN.
- VMII low-voltage battery CAN protocol.
- SRNE WOW BMS Modbus Protocol for CAN.
- INVT BMS and PCS CAN protocol.

Why it still needs validation:

- The CAN baud rate, termination requirements, and available message set still need to be confirmed.
- CAN may expose an inverter-oriented summary rather than every detailed BMS value.
- RS485 and RS232 remain useful fallback paths because the table also shows RS485-1 and RS232 as host-computer-capable ports.

## Validation Plan

1. Confirm the Cubix 100 physical CAN RJ45 pinout against the actual battery label/manual revision.
2. Confirm termination requirements.
3. Confirm battery protocol configuration in the Eco-Worthy app or BMS tool. Start with the default Pylon CAN protocol.
4. Connect the SH-C31G to the Raspberry Pi and confirm Linux enumeration.
5. Bring up SocketCAN or `slcand`.
6. Capture passive CAN traffic without sending control frames.
7. Compare decoded voltage, current, SOC, alarms, and temperature against the Eco-Worthy app.
8. Keep RS485 available as a fallback if CAN exposes only inverter-facing summary messages.

## Cable Build Notes

For final installation, use separate labeled Cat-6 cables for CAN and RS485 rather than combining both protocols in one cable.

Recommended labels:

- `BAT-CAN`
- `BAT-RS485-1`

The male RJ45 screw-terminal breakout is for experimentation and pinout confirmation. For final wiring, prefer crimped RJ45 plugs at the battery side with strain relief and a documented adapter-side termination into the CAN or RS485 converter.

## CAN Termination Check

Current expectation: the SH-C31G likely has onboard switchable 120 ohm CAN termination because it is based on CANable 2.0. However, verify the actual unit rather than assuming the listing text is complete.

When the adapter arrives:

1. Look for a miniature switch or marking such as `120R`, `R120`, or `TERM`.
2. With the adapter disconnected from the battery, measure resistance across CANH and CANL with a multimeter.
3. Toggle the termination switch, if present.
4. Expect roughly 120 ohms when termination is enabled, and open/high resistance when disabled.

For the battery bench test, enable termination at the SH-C31G if the adapter is one physical end of the CAN bus. Confirm whether the Cubix battery provides termination internally before adding a second terminator.

## RS485 / RS232 Fallback Hardware

Recommended fallback class: isolated USB-to-RS485 adapter using an FTDI or similarly well-supported USB serial chipset.

Good candidate:

- Waveshare USB TO RS485/422 industrial isolated converter.

Why it fits:

- USB-attached, so it should work with the Raspberry Pi 3 Model B v1.2 without consuming GPIO UART pins.
- Provides galvanic isolation between the Pi and RS485 side.
- Includes ESD/surge protection and configurable 120 ohm termination.
- Uses screw terminals for A/B/GND wiring.
- Supports Linux.

Lower-cost candidate:

- DSD TECH SH-U10 USB-to-RS485 adapter.

Why it is less ideal:

- It is simple and Linux-compatible, but it is not the preferred first choice for a power-system cabinet because the project should favor isolation on field wiring.

Cubix 100 RS485-1 pinout from the protocol table:

| RJ45 Pin | Signal |
|---:|---|
| 1, 8 | RS485-B1 |
| 2, 7 | RS485-A1 |
| 3, 6 | GND |
| 4, 5 | NC |

Cubix 100 RS232 pinout from the protocol table:

| RJ45 Pin | Signal |
|---:|---|
| 1, 2 | NC |
| 3 | TX |
| 4 | RX |
| 5 | GND |
| 6 | 14 V |

RS232 is useful for host-computer tools, but avoid it as the first Pi integration path unless the voltage and cable wiring are very carefully controlled. The 14 V pin must not be connected to a USB serial adapter.

## Decision

Buy/keep the SH-C31G as the preferred CAN test adapter for the project. The protocol table makes CAN worth testing, and the SH-C31G's isolation plus cabled USB Type-B form factor make it a better cabinet choice than the SH-C31A dongle style. Do not design production monitoring around CAN until traffic capture proves it exposes the required BMS telemetry.
