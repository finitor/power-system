# Supervisory Controller

## Hardware

| Item | Value |
|---|---|
| Controller | Raspberry Pi 3 Model B v1.2 |
| Power source | TBD DC-DC supply from 48 V system; target 5.1 V, 5 A or better |
| Network | 100BASE-T Ethernet, 2.4 GHz Wi-Fi, Bluetooth/BLE available |
| Storage | microSD boot plus external logging storage candidate |
| Enclosure | TBD |
| Battery CAN interface | DSD TECH SH-C31G isolated USB-to-CAN adapter, preferred candidate |
| Battery RS485 interface | Waveshare isolated USB-RS485/422 adapter, fallback |
| Magnum RS485 interface | Second isolated USB-RS485 adapter, pilot |

## Power Supply Requirements

The Raspberry Pi 3 Model B normally wants a 5 V / 2.5 A supply before allowing for hats, USB adapters, or a display. For this installation, size the 5 V rail with extra headroom because the Pi may power multiple USB comms adapters, hats, and a small display.

Recommended DC-DC converter class:

- Input range comfortably above the full battery charge voltage, not just nominal 48 V.
- At least 5 A output at 5 V or 5.1 V.
- Prefer an industrial DIN/wall-mount converter with screw terminals, fuse protection, and documented thermal derating.
- Consider isolation if noise or ground-loop behavior becomes a problem.

Do not use a converter rated only 10-55 V input as the final Pi supply. A 48 V LiFePO4 battery bank can reach about 58.4 V while charging, which exceeds a 55 V input rating. A 3 A / 15 W output is also marginal once hats and a display are added.

## 5 V Load Budget

Use this as a design budget, not a measured-current table. The estimates intentionally include startup and unknown-device headroom.

Known or likely 5 V loads:

| Load | Count | 5 V Current Budget | Notes |
|---|---:|---:|---|
| Raspberry Pi 3 Model B v1.2 | 1 | 2.5 A | Official-class supply budget for the Pi before adding a comfortable external-peripheral margin |
| DSD TECH SH-C31G USB-CAN adapter | 1 | 0.5 A | USB-powered isolated CAN adapter; actual draw likely lower |
| Waveshare isolated USB-RS485 adapter | 1 | 0.3 A | Battery RS485 fallback |
| Magnum isolated USB-RS485 adapter | 1 | 0.3 A | Dedicated inverter network adapter |
| DS18B20 sensor bus | TBD | 0.05 A | Multiple probes are still small load |
| ASAIR AM2302 / DHT22 | 1 | 0.01 A | Ambient humidity/temperature |
| ADS1015 ADC module | 1 | 0.01 A | Prototype analog sensing |
| GPIO extension board | 1 | 0.05 A | Board itself is near-zero unless LEDs/loads are attached |
| External USB logging storage | 1 | 0.5-1.0 A | Use 1.0 A if USB SSD is selected |
| Small local display | 1 | 0.5-1.0 A | TBD; omit if no display |

Current known subtotal without display or USB SSD: about 3.7 A.

Practical design targets:

| Build | Suggested 5 V Supply |
|---|---:|
| Pi + CAN + two RS485 adapters + sensors | 5.1 V, 5 A minimum |
| Add USB SSD or powered-card logging from the 5 V rail | 5.1 V, 6 A minimum |
| Add small display or leave expansion room | 5.1 V, 8-10 A preferred |

If an industrial USB hub is powered from a 12 V rail, its attached USB devices still consume system power, but that power is supplied by the hub's converter instead of through the Pi's USB ports. That is better for Pi stability, but the 12 V rail and upstream DC-DC converter must then include the hub load.

The earlier 5.1 V / 5 A recommendation is the lower bound for a no-display build. For the most flexible final cabinet, a 30 W to 60 W class 5 V supply is more comfortable.

## USB Port Budget

The Raspberry Pi 3 Model B has four USB 2.0 host ports. The current telemetry hardware plan fits, but only with modest spare room:

| Port Use | Device | Notes |
|---|---|---|
| USB 1 | Battery CAN adapter | DSD TECH SH-C31G |
| USB 2 | Battery RS485 adapter | Waveshare isolated adapter for Eco-Worthy fallback |
| USB 3 | Magnum RS485 adapter | Dedicated isolated adapter for inverter network experiments |
| USB 4 | Spare / display / service | Keep available if possible |

Avoid combining the battery RS485 fallback and Magnum network on one adapter. They are separate buses with different pinouts, protocols, and risk profiles. If more USB devices are added, use a powered industrial USB hub rather than relying on the Pi to power every adapter and display directly.

## Storage And Logging

The Pi's internal microSD card should not be treated as the only durable telemetry store. For robust logging, prefer separating the boot medium from the write-heavy telemetry medium.

Recommended storage hierarchy:

- Best final option: USB SSD on a powered hub, mounted by UUID for telemetry logs and database files.
- Acceptable pilot option: high-endurance or industrial SD card in a reliable USB card reader.
- Convenience option: SD slot built into a powered USB hub, used only after soak testing for disconnects and power-cycle behavior.
- Baseline only: normal microSD card for both OS and logs.

A large-format SD card is not automatically more durable than microSD. Durability depends on the card class and controller: high-endurance, industrial, pSLC, or SLC media is more relevant than physical card size alone.

Logging design notes:

- Keep the OS on the Pi microSD initially and put telemetry writes on external storage.
- Use log rotation and retention limits.
- Consider batching writes instead of flushing every single sample.
- Mount the external store with conservative `nofail` behavior so the Pi can boot even if the logging disk is absent.
- Alert when external logging storage is missing, read-only, nearly full, or showing I/O errors.
- Consider read-only root or overlay filesystem later if field reliability becomes a problem.

## Responsibilities

- Collect telemetry from the battery bank, charge controller, inverter/charger, and local sensors.
- Publish normalized telemetry to local topics.
- Store telemetry locally.
- Provide a local dashboard.
- Raise alerts for unsafe or unusual conditions.
- Coordinate only explicitly documented, non-critical control actions.

## Non-Responsibilities

- Primary battery protection.
- Charge regulation.
- Inverter safety shutdown.
- Overcurrent protection.
- Any safety function that must work when the Pi is powered off, rebooting, or crashed.

## Telemetry Goals

| Measurement | Source | Priority | Notes |
|---|---|---|---|
| Pi uptime | OS | Medium | Service reliability |
| Pi temperature | OS | Medium | Enclosure thermal behavior |
| Disk usage | OS | Medium | Prevent logging failures |
| Service health | systemd / app health checks | High | Include telemetry and dashboard services |
| Network status | OS | Medium | Local operation should continue without internet |
| CAN adapter health | SocketCAN / adapter service | High | Confirm SH-C31G enumerates consistently on Raspberry Pi OS |
| Utility-room humidity | ASAIR AM2302 / DHT22 | Medium | Useful because water equipment is present in the utility room |
| Utility-room ambient temperature | ASAIR AM2302 / DHT22 | Medium | Environmental context; not a heater safety sensor |

## Environmental Sensors

Use DS18B20 probes for battery, heater, and enclosure temperature control because they are addressable and better suited to multi-point thermal monitoring. Use the ASAIR AM2302/DHT22 as a separate ambient utility-room sensor for humidity and general room temperature.

The AM2302 is useful for:

- Utility-room humidity trend.
- Water-equipment leak/condensation risk context.
- General ambient temperature around the controller cabinet.

Do not use the AM2302 as the only heater cutoff or battery-temperature permissive sensor.

## Open Questions

- Does the Pi need a UPS or graceful shutdown circuit?
- What storage medium is reliable enough for telemetry logging?
- Should final telemetry storage be a USB SSD instead of SD-card media?
- Should the SH-C31G run candlelight firmware or slcan firmware for this project?
- Should the first battery integration use CAN, RS485, or both for comparison?
- Does the final enclosure need a powered USB hub for service access or a display?
