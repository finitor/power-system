# Inverter/Charger

## Hardware

| Item | Value |
|---|---|
| Product family | MagnaSine |
| Model | 4448 |
| Role | 48 V inverter/charger |
| DC input | 48 V nominal |
| AC output | TBD |
| AC input / generator input | TBD |
| Installed remote | Magnum ME-RC50 |
| Communication interface | Magnum proprietary RJ11 remote/network ports; Pi integration TBD |

## Telemetry Goals

| Measurement | Source | Priority | Notes |
|---|---|---|---|
| DC input voltage | Inverter/charger | High | Compare against battery-bank voltage |
| AC output power/load | Inverter/charger | High | Core load telemetry |
| AC output voltage/frequency | Inverter/charger | Medium | Useful for quality and fault diagnosis |
| Charger state | Inverter/charger | High | Charging, float, standby, fault, etc. |
| AC input state | Inverter/charger | Medium | Generator/grid input availability if used |
| Inverter temperature | Inverter/charger | Medium | Thermal monitoring |
| Faults/alarms | Inverter/charger | High | Needs exact message mapping |

## Control Boundaries

The inverter/charger owns AC inversion and charging behavior. The Raspberry Pi may monitor state and may later request mode changes only if a supported, well-documented interface is available.

The charger side must be treated as legacy lead-acid-oriented equipment until its exact charge profile behavior is verified. If AC input or generator charging is enabled, the supervisor needs to prevent sustained high-voltage float from becoming the normal LiFePO4 resting state. Equalization must be disabled for normal operation.

Possible future supervisory actions:

- Alert on inverter fault or overload.
- Alert on unexpected AC input/output state.
- Log charging sessions from AC input.
- Coordinate non-critical load shedding before low-voltage shutdown.
- Detect excessive charger time in absorb or float.
- Alert immediately if the charger enters equalize.
- Keep equalize disabled or locked behind a deliberate manual procedure.
- If Magnum network command behavior is verified, request charger standby/off after a full-charge condition.
- Preserve local ME-RC50 manual control as the trusted override path.
- On BMS charge-disallow or approaching overvoltage/low-temperature limit, stop AC charging before the battery BMS opens.
- If hardware interruption is needed, interrupt AC input or generator-start permission before interrupting the inverter/charger DC battery connection.

## Wiring And Communications

The existing phone-style cable to the ME-RC50 remote is an important clue: this inverter is already using Magnum's remote/control ecosystem. It should not be treated as generic serial, Ethernet, CAN, RS485, or Modbus unless a documented Magnum interface is identified.

The MS-PAE manual identifies these low-voltage accessory ports:

| Label | Connector | Purpose | Notes |
|---|---|---|---|
| Red | RJ45 | Parallel stack / ME-RTR router | Not Ethernet. Do not connect to a network switch or Pi Ethernet port. |
| Green | RJ11 | Magnum Net accessories | For network-capable Magnum accessories such as auto-gen-start or battery monitor modules. |
| Blue | RJ11 | Remote control display | Existing phone-style remote cable likely lands here. Proprietary Magnum communication. |
| Yellow | RJ11 | Battery temperature sensor | Dedicated BTS accessory port. |

The ME-RC50 manual describes the remote cable as a 4-conductor twisted-pair telephone cable with RJ11 connectors at both ends. The inverter powers the remote over that cable, and the remote reports a communication fault if it stops receiving data over the Magnum Network.

The ME-RC50 provides local human access to:

- Inverter and charger enable/disable controls.
- Shore/input current limit settings.
- Inverter/charger DC meter values.
- Charger and inverter setup parameters.
- System status and fault messages.
- Optional networked accessory menus, including ME-AGS-N auto-generator-start and ME-BMK battery monitor functions if those accessories are installed.

Document:

- DC cable path from 48 V bus to inverter.
- DC disconnect and overcurrent protection.
- AC output panel or load connection.
- AC input/generator wiring, if present.
- Remote panel or network accessory model.
- Communication cable type and routing.

## Pi Monitoring Strategy

Preferred future path:

- Confirm the installed ME-RC50 revision and firmware behavior from the TECH menu.
- Identify any Magnum network accessories.
- Evaluate the Magnum RS485 prior art in [Magnum Inverter Interface Research](../research/magnum-inverter-interface.md).
- Check whether a compatible Magnum data gateway or web/network accessory is available.
- Use supported Magnum accessories for direct inverter state if available.

Fallback monitoring path:

- Infer inverter behavior from battery BMS or shunt current, AC output/input sensing, and remote-panel observations.
- Keep Pi control of inverter/charger modes conservative until a supported interface is verified.

## Open Questions

- What is the exact manufacturer/model marking: Magnum Energy, MagnaSine, MS4448PAE, or another variant?
- What ME-RC50 revision is installed?
- Are any Magnum Net accessories installed, such as battery monitor or auto-gen-start modules?
- Is there a compatible Magnum data gateway or web/network interface still available?
- Can telemetry be read locally from the Pi without reverse-engineering the remote protocol?
- Can an isolated RS485 adapter safely monitor the Magnum network in parallel with the ME-RC50?
- Can charger enable/disable or standby be controlled separately from inverter on/off?
- How is equalization disabled from the ME-RC50 or inverter configuration, and can the supervisor verify that state?
- Can the charger be placed in standby/off without disabling AC inversion?
- Is an AC-input contactor or generator inhibit needed as a hardware fallback for charge inhibit?
- Which settings should remain manual-only?
- What are the inverter/charger fault states and recovery procedures?
