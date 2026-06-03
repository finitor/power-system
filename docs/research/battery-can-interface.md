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

## Current Status

Status as of 2026-05-29: first passive CAN capture succeeded from Eco-Worthy Cubix 100 battery 1 using the DSD TECH SH-C31G on Raspberry Pi `blueberry`.

Observed bench wiring and bus state:

- Battery CAN RJ45 pin 4 wired to SH-C31G `CANH`.
- Battery CAN RJ45 pin 5 wired to SH-C31G `CANL`.
- Battery CAN RJ45 pin 3 or 6 wired to SH-C31G `GND`.
- Adapter enumerated as native SocketCAN: `1d50:606f OpenMoko, Inc. Geschwister Schneider CAN adapter`.
- Linux interface appeared as `can0`.
- `can-utils` was installed on the Pi for `candump`.
- `can0` was brought up listen-only at 500 kbit/s.
- `ip -details -statistics link show can0` reported `ERROR-ACTIVE`, 500000 bit/s, listen-only, 0 bus errors, 0 bus-off, and 0 transmitted packets.
- `candump can0` showed repeated frames at 500 kbit/s. Frame `35E` carried ASCII `PYLON`, matching the expected default Pylon CAN protocol family.

Representative passive capture:

```text
can0  351   [8]  48 02 D0 07 D0 07 C0 01
can0  355   [8]  1E 00 64 00 00 00 00 00
can0  356   [8]  79 14 00 00 71 00 00 00
can0  359   [8]  00 00 00 00 02 50 4E 00
can0  35C   [8]  C0 00 00 00 00 00 00 00
can0  35E   [8]  50 59 4C 4F 4E 20 20 20
can0  35F   [8]  00 00 0D 05 00 00 00 00
can0  370   [8]  76 00 6C 00 CF 0C CA 0C
can0  371   [8]  04 01 02 02 06 02 04 02
can0  372   [8]  02 00 00 00 00 00 00 00
can0  373   [8]  CA 0C CF 0C 1B 01 1C 01
can0  374   [8]  30 32 30 34 00 00 00 00
can0  375   [8]  30 32 30 36 00 00 00 00
can0  376   [8]  30 32 30 32 00 00 00 00
can0  377   [8]  30 32 30 32 00 00 00 00
can0  379   [8]  C8 00 00 00 00 00 00 00
```

The bus is now proven electrically connected and readable from the Pi. It is not yet proven to expose all telemetry or any safe control surface needed by the supervisor.

## Decoded Telemetry

Initial decoder command on the Pi:

```sh
PYTHONPATH=software/pi-controller/src .venv/bin/python -m offgrid_power.cli.can_decode --interface can0 --seconds 3 --raw
```

Live decoded result on 2026-05-29:

| CAN ID | Status | Decoded meaning | Observed value |
|---:|---|---|---|
| `0x351` | Supported by Pylon document | Charge/discharge limits | Charge voltage `58.4 V`, charge current `200.0 A`, discharge current `200.0 A`, discharge floor `44.8 V` |
| `0x355` | Supported by Pylon document | SOC / SOH | SOC `30%`, SOH `100%` |
| `0x356` | Supported by Pylon document | Pack voltage/current/temperature | `52.41 V`, `0.0 A`, `11.3 C` |
| `0x359` | Supported by Pylon document | Protection/alarm flags and module count | `2` modules, marker `PN`, no protection flags, no alarm flags |
| `0x35C` | Supported by Pylon document | Battery request/permission flags | Charge enabled, discharge enabled |
| `0x35E` | Supported by Pylon document | Manufacturer string | `PYLON` |
| `0x373` | Tentative extended decode | Min/max cell voltage and temperature | Cell voltage about `3.274-3.278 V`, cell temperature about `10.9-10.9 C` |
| `0x379` | Tentative extended decode | Installed capacity | `200 Ah` |
| `0x35F`, `0x370`, `0x371`, `0x372`, `0x374`, `0x375`, `0x376`, `0x377` | Undecoded | Likely extended status, identity, index, or firmware fields | Preserve raw bytes until validated |

Interpretation so far:

- CAN exposes enough high-level battery telemetry to be useful immediately: SOC, SOH, pack voltage, pack current, temperature, module count, BMS protection/alarm flags, charge/discharge current limits, voltage limits, and charge/discharge enable state.
- CAN appears to expose some per-cell or pack-detail summary via extended frames, at least min/max cell voltage and temperature, but this needs validation against the Eco-Worthy app before being treated as canonical.
- CAN does not yet show evidence of a writable control API for the Pi. The `0x35C` frame should be treated as a battery-to-inverter request/permissive signal, not as a control command we can send.
- If the project needs per-pack details, configuration, firmware state, cell-by-cell values, or active BMS configuration/control, RS485 or vendor tooling may still be required.
- If the project only needs supervisory telemetry and safe charger/load policy inputs, CAN may be sufficient once the decoded fields are validated across idle, load, charge, and two-pack operation.

Reference: the [Pylon CANBUS protocol document](https://akkudoktor.net/uploads/short-url/oLZIl9bFdMC1doN4OnIvXbazHMl.pdf) describes standard frames, 500 kbit/s bus speed, 1 second transmission cycle, little-endian encoding, inverter heartbeat `0x305`, and core IDs `0x351`, `0x355`, `0x356`, `0x359`, `0x35C`, and `0x35E`.

## Preliminary Assessment

Status: solid candidate for CAN telemetry, with documented Cubix 100 CAN pinout, observed 500 kbit/s traffic, and Pylon-protocol identification in captured frames. Still decode and validate the message set before making it the production battery telemetry path.

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

What still needs validation:

- The available message set still needs to be decoded and compared against independent readings.
- CAN may expose an inverter-oriented summary rather than every detailed BMS value.
- RS485 and RS232 remain useful fallback paths because the table also shows RS485-1 and RS232 as host-computer-capable ports.

## Validation Progress

Completed:

1. Confirmed working battery CAN RJ45 wiring path: pin 4 `CANH1`, pin 5 `CANL1`, pin 3 or 6 `GND`.
2. Confirmed adapter-side termination switch behavior: about 125 ohms across CANH/CANL with switch 1 up and switch 2 down.
3. Confirmed total bench bus termination around 63 ohms when battery and adapter are connected, consistent with two terminators in parallel.
4. Confirmed the SH-C31G enumerates as native SocketCAN on Raspberry Pi OS.
5. Confirmed `can0` reads passive traffic at 500 kbit/s.
6. Confirmed captured frames include the `PYLON` protocol identifier.

Remaining:

1. Confirm the selected battery protocol in the Eco-Worthy app or BMS tool, starting with the default Pylon CAN protocol.
2. Validate decoded IDs `351`, `355`, `356`, `359`, `35C`, `35E`, `373`, and `379` against independent readings.
3. Compare decoded voltage, current, SOC, alarms, limits, and temperatures against the Eco-Worthy app and ESM-100 monitor.
4. Repeat capture with pack 2 connected and then with both packs connected as the final bank topology.
5. Determine whether CAN exposes per-pack telemetry or only aggregate inverter-facing values.
6. Keep RS485 available as a fallback if CAN exposes only summary telemetry.

## Next Investigation Steps

Use a passive-first process until the message meanings and any write/control behavior are understood.

1. Record a baseline capture with battery idle, no charger, and a known ESM-100/app reading.
2. Record a second capture while applying a small known load, then compare which bytes change with current, voltage, SOC, and temperature.
3. Record a third capture while charging, if safe, to determine current sign convention and charge/discharge limit reporting.
4. Validate candidate decodes against the ESM-100 and Eco-Worthy app over several operating points.
5. Inspect alarms/status frames by comparing normal operation with documented non-invasive state changes only, such as charger enabled/disabled or pack online/offline. Do not induce fault conditions just to learn bits.
6. Decide whether the supervisor should consume CAN directly, consume RS485 instead, or use CAN for high-level battery summary plus another source for per-pack details.
7. Treat CAN control as unavailable until proven otherwise from vendor documentation or a reversible bench-only test plan. Do not transmit BMS/inverter control frames from the Pi during telemetry validation.

## Cable Build Notes

For final installation, use separate labeled Cat-6 cables for CAN and RS485 rather than combining both protocols in one cable.

Recommended labels:

- `BAT-CAN`
- `BAT-RS485-1`

The male RJ45 screw-terminal breakout is for experimentation and pinout confirmation. For final wiring, prefer crimped RJ45 plugs at the battery side with strain relief and a documented adapter-side termination into the CAN or RS485 converter.

## CAN Termination Check

Current expectation: the SH-C31G likely has onboard switchable 120 ohm CAN termination because it is based on CANable 2.0. However, verify the actual unit rather than assuming the listing text is complete.

Bench measurement on the DSD TECH SH-C31G in hand: with switch 1 up and switch 2 down, resistance across CANH and CANL measured about 125 ohms. Treat this switch position as adapter termination enabled.

When the adapter arrives:

1. Look for a miniature switch or marking such as `120R`, `R120`, or `TERM`.
2. With the adapter disconnected from the battery, measure resistance across CANH and CANL with a multimeter.
3. Toggle the termination switch, if present.
4. Expect roughly 120 ohms when termination is enabled, and open/high resistance when disabled.

For the battery bench test, enable termination at the SH-C31G if the adapter is one physical end of the CAN bus. Confirm whether the Cubix battery provides termination internally before adding a second terminator.

2026-05-29 bench observation: with the battery powered off/quiet and the adapter connected, resistance at the adapter terminals measured about 63 ohms. That is consistent with one terminator at the battery side plus the SH-C31G terminator in parallel. With the battery powered, resistance measurements were misleading; use ohms mode only on an unpowered or quiet bus.

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

The short cable supplied with the Eco-Worthy ESM-100 remote display is RJ12 6P6C straight-through based on wire-color inspection at the connectors. Treat this as an observed cable fact, not yet a vendor specification.

The Midnite Classic 200 remote/display cable should not be treated as interchangeable with the Cubix remote cable. It is also RJ12 6P6C, but it is reversed/rolled: the pin positions are mirrored end-to-end, so pin 1 maps to pin 6, pin 2 maps to pin 5, and so on.

RS232 is useful for host-computer tools, but avoid it as the first Pi integration path unless the voltage and cable wiring are very carefully controlled. The 14 V pin must not be connected to a USB serial adapter.

## Decision

Buy/keep the SH-C31G as the preferred CAN test adapter for the project. The protocol table makes CAN worth testing, and the SH-C31G's isolation plus cabled USB Type-B form factor make it a better cabinet choice than the SH-C31A dongle style. Do not design production monitoring around CAN until traffic capture proves it exposes the required BMS telemetry.
