# Magnum Inverter Interface Research

## Context

The inverter/charger appears to be a Magnum MS4448PAE-class unit with an ME-RC50 remote. The existing phone-style remote cable indicates that the inverter is using Magnum's proprietary remote/accessory network.

## Current Hardware Status

Dedicated pilot adapter on order, expected 2026-06-05: Waveshare USB TO RS485/422 Industrial Grade Isolated Converter, with onboard original FT232RL and SP485EEN, multiple protection, wall-mount support, and rail-mount support.

Use this adapter only for the Magnum network pilot. Do not share it with the battery RS485 fallback bus.

## Prior Art

### pyMagnum

Project: https://pymagnum.readthedocs.io/

Notes:

- Describes the Magnum Energy network as a proprietary protocol carried over RS485.
- States that the inverter sends a packet roughly every 100 ms and the remote replies with settings and optional commands.
- Identifies fields such as inverter mode, fault, DC voltage/current, AC input/output values, charger state, temperatures, model, and accessory data.
- Treat as primarily read-oriented prior art.

### magnum-pi

Project: https://pypi.org/project/magnum-pi/

Notes:

- Newer async Python package for sniffing, decoding, and transmitting Magnum network packets.
- Claims the bus is two-wire RS485 at 19200 baud, 8N1.
- Documents connection to the Magnum Network RJ11 port or the daisy-chain port on an ME-RC remote.
- States pin 1 is Data+ and pin 4 is Data-.
- Recognizes 48 V MS-series models including MS4448PAE.
- Claims CLI support for sending inverter commands, including inverter toggle.

This is promising but should be treated as experimental until tested on the bench with the actual inverter and remote.

### MagWeb

Manual: https://www.magnum-dimensions.com/sites/default/files/product/manual/sensata-magweb-wired-ethernet-monitoring-kit-mw-owner-manual.pdf

Notes:

- Official Magnum accessory path for web monitoring.
- Installs inline between the inverter remote port and the remote control.
- Uses the same four-conductor RJ11 remote cable on the inverter/remote side.
- May be useful if a compatible unit is obtainable, but cloud/service longevity and local API access need verification.

## Recommended Way Forward

1. Keep the ME-RC50 installed and working as the trusted manual control.
2. Add a separate isolated USB-RS485 adapter for Magnum network experiments instead of reusing the battery RS485 adapter.
3. Build an RJ11 breakout/test cable so the Pi can listen to the Magnum bus without disturbing the remote.
4. Start in listen-only mode with `pymagnum` or `magnum-pi` and confirm decoded model, mode, fault, DC voltage, AC values, charger state, and inverter on/off state.
5. Only after reliable passive decoding, test an explicit inverter toggle command in a controlled session with local access to the remote and AC loads disconnected or non-critical.
6. Expose Pi control as a guarded command, not an automatic policy at first.

## Hardware Implications

Add for pilot testing:

- Waveshare USB TO RS485/422 Industrial Grade Isolated Converter, on order expected 2026-06-05, dedicated to the Magnum network.
- RJ11 6P4C or 6P6C breakout connectors.
- Short RJ11 telephone patch cables.

Do not connect the Magnum RJ45 stack/router port to Ethernet. It is not an Ethernet port.

## Bench RJ45 Breakout Wiring

Current bench cable path: straight-through 4-wire RJ11 cable from the MagnaSine network port, into an RJ45 straight-through coupler, then Cat-6 patch cable to an RJ45 screw-terminal breakout.

Measured on 2026-06-05:

| RJ45 breakout pin | Expected signal | Measurement / note |
|---:|---|---|
| 3 | RS485 B / D- | 0.52-0.57 V to pin 5 |
| 4 | +14 V accessory power | 14.14 V to pin 5 |
| 5 | GND | Reference |
| 6 | RS485 A / D+ | 3.85-3.91 V to pin 5; 3.28-3.37 V to pin 3 |

Expected USB-RS485 adapter wiring for the first bench attempt:

| RJ45 breakout | USB-RS485 adapter |
|---|---|
| Pin 6 | A / D+ / 485+ |
| Pin 3 | B / D- / 485- |

Leave RJ45 pin 4 (+14 V) and pin 5 (GND) disconnected from the USB adapter. If no packets decode, swap pins 3 and 6 at the adapter before assuming a protocol or software problem, since some adapters label A/B backward.

## Control Policy

For inverter on/off, use a state-aware command path:

- Read current inverter state from the bus.
- If the desired state already matches, do nothing.
- If a state change is needed, issue one toggle command.
- Re-read state and alert if it did not change.
- Rate-limit commands and require local/manual enable during early testing.

Avoid blind repeated toggles because the protocol exposes inverter on/off as a toggle-style command in the prior art.

## Open Questions

- Which physical port is easiest and safest to tap: inverter network port, inverter remote port, or ME-RC50 daisy-chain port?
- Does the installed ME-RC50 revision behave as expected with `pymagnum` or `magnum-pi`?
- Is a MagWeb unit already installed, available used, or worth buying?
- Can Magnum network transmit commands coexist safely with the ME-RC50 remote in normal service?
