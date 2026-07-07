# Network Stack Map

This document compares the communication stacks used by the power-system
appliances. It is an orientation map, not the canonical pinout or protocol
reference. Keep wiring details in [wiring.md](wiring.md), device behavior in the
subsystem docs, and reverse-engineering notes under [research/](research/).

## Layer Model

Use these layers when debugging or documenting a telemetry/control path:

| Layer | Meaning in this system |
|---|---|
| Physical | Electrical signaling and connector: Ethernet, RS485, CAN, 1-Wire, GPIO, USB |
| Link/framing | How bytes or frames are delimited, addressed, checked, acknowledged, or rejected |
| Session/ownership | Whether the Pi is master, passive listener, peer, or hardware endpoint |
| Application protocol | Device-specific meaning: Modbus registers, CAN profile, Magnum packets, sensor files |
| Supervisor adapter | Code that turns device data into normalized snapshots, metrics, controls, and warnings |

The physical layer alone does not describe reliability. Magnum and EPEver both
use RS485, but EPEver speaks Modbus RTU with CRC while Magnum uses a proprietary
timing-framed bus with no CRC.

## Appliance Stacks

| Appliance/path | Physical | Link/framing | Session/ownership | Application protocol | Supervisor adapter | Integrity and failure shape |
|---|---|---|---|---|---|---|
| MidNite Classic 200 | Ethernet via Pi LAN | TCP/IP | Pi is Modbus TCP client | Classic Modbus register map | `ClassicClient` | TCP handles framing/retry below Modbus; Modbus exceptions/timeouts are explicit. Network loss affects LAN reachability too. |
| EPEver TEP10425 COM | RS485 via `/dev/epever-rs485` | Modbus RTU with unit id, function code, byte counts, CRC-16 | Pi is Modbus master | EPEver register map | `EpeverClient` | Noisy frames should fail CRC or timeout rather than decode as plausible telemetry. |
| Magnum MS4448PAE network tap | RS485 via `/dev/magnum-rs485` | Proprietary timing-gap framing; no delimiter, length prefix, or CRC | Pi is passive listener on inverter/remote chatter | Magnum inverter and remote packets | `MagnumClient` | Fragile: bad framing can produce missed polls or plausible remote-packet decodes. We key inverter packets on the MS4448 model byte and guard remote settings with plausibility/repeat checks. |
| Eco-Worthy Cubix battery CAN | CAN via SocketCAN `can0` | CAN data link: frame boundaries, arbitration, ACK, CRC, controller error counters | Pi is passive CAN listener | Pylon-compatible or selected BMS CAN profile | `BatteryCanClient` | Corrupt frames are rejected by CAN hardware/driver; failures are more observable through missing frames or CAN error state. |
| Eco-Worthy Cubix service RS485 | RS485 via `/dev/cubix-rs485` when connected | Vendor service protocol over serial | Bench/service tool owns the bus | Eco-Worthy companion/service protocol | Research/bench path only | Not part of normal supervisor telemetry. Keep separate from Magnum/EPEver RS485 buses. |
| Ambient DS18B20 | 1-Wire on GPIO | 1-Wire sysfs/w1 framing and CRC in sensor readout | Pi is bus master | Linux w1 sensor files | `AmbientDs18b20Client` | Kernel driver exposes CRC-valid reads; disconnected/zero readings are filtered in adapter code. |
| Ambient DHT | Single-wire GPIO | DHT timing protocol with checksum | Pi initiates read | DHT temperature/humidity payload | `AmbientDhtClient` | Sensor checksum rejects many bad reads; advisory only. |
| Relay/thermostat controls | GPIO/relay contacts | Discrete digital state | Pi drives permissive/control outputs | Relay policy, not a data protocol | `RelaySupervisor` | No packet integrity; safety must come from conservative wiring, fail-off states, and external thermostats/cutoffs. |
| Wall/display/API clients | Ethernet/Wi-Fi HTTP | TCP/IP + HTTP | Display/client polls Pi | Supervisor JSON API / rendered pages | `web_display`, terminal clients | Uses normal network stack; stale/unavailable display data is a presentation problem, not appliance telemetry truth. |

## Current Stable Device Names

The supervisor uses stable OS-level names before any appliance protocol code runs:

| Name | Current binding | Why |
|---|---|---|
| `/dev/magnum-rs485` | CH340 `1a86:7523` on powered hub path `1.3.2` | CH340 has no unique serial; identity follows the physical port. |
| `/dev/epever-rs485` | CH340 `1a86:7523` on powered hub path `1.3.3` | Same serial-less CH340 issue; identity follows the physical port. |
| `/dev/magnum-rs485-pl2303` | PL2303 SH-U11H serial `DZBSb11CN12`, if installed | Named fallback/spare, not canonical Magnum. |
| `/dev/magnum-rs485-ft232r` | FT232R serial `BG041BAY`, if installed | Trial fallback; direct-port CH340 remains canonical. |
| `/dev/cubix-rs485` | FT232R serial `A50285BI`, if installed | Bench/service RS485 path. |
| `can0` | DSD TECH SH-C31G / gs_usb | SocketCAN netdev, not a tty. |

See [config/udev/90-offgrid-usb.rules](../config/udev/90-offgrid-usb.rules) for
the actual binding rules.

## Debugging By Layer

When something fails, identify the lowest layer that is known-good before
changing higher-level code.

| Symptom | Start at this layer | Useful discriminator |
|---|---|---|
| Device node missing | Physical/USB enumeration | `lsusb`, `/dev`, `udevadm info`, udev symlink target |
| Serial device opens but no response | Physical/link/session | Wiring polarity, ground reference, baud, bus ownership, timeout logs |
| Modbus CRC/timeout | RS485 physical or Modbus request/response | EPEver failures should be explicit CRC/timeout/exception, not plausible garbage |
| Magnum `no valid inverter packet` | Proprietary framing/application identification | We may be seeing bytes, but no packet with model byte `0x73` within the poll window |
| Magnum settings flash/change | Remote-packet application decode | Inverter packet may be valid while remote settings decode is intermittent or bogus |
| CAN silence | CAN physical/link or BMS source behavior | SocketCAN state, error counters, frame presence, adapter enumeration |
| All USB/Ethernet transports vanish | Pi USB/power layer | Brownout or USB hub reset; restart alone may not recover de-enumerated devices |

## Design Implications

- Prefer protocols that reject bad frames low in the stack. CAN and Modbus RTU are
  easier to trust than timing-framed proprietary serial packets.
- Keep separate physical adapters for separate buses unless there is a deliberate
  design to share one bus. The current Magnum and EPEver RS485 paths are separate
  electrical networks with different protocols and risk profiles.
- Treat Magnum remote settings as advisory/static display data, not a robust
  control source, unless the protocol is further validated or replaced by a
  supported Magnum accessory/interface.
- Persist interface health metrics where possible. Display-only rolling error
  rates are useful for watching, but durable per-layer counters are better for
  post-mortems.
