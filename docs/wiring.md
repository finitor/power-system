# Wiring

Use this file as the canonical wiring record. Update it whenever a physical connection changes.

## Raspberry Pi Pinout

| Pi Pin | GPIO | Signal | Connected Device | Notes |
|---:|---:|---|---|---|
| 1 | 3.3 V | 3.3 V power | TBD | Do not overload Pi rail |
| 6 | GND | Ground | TBD | Common logic ground |

## Power Wiring

| From | To | Voltage | Cable Gauge | Fuse / Breaker | Notes |
|---|---|---:|---|---|---|
| Eco-Worthy Cubix 100 battery 1 | 48 V DC bus / battery combiner | 48 V nominal | TBD | TBD | Confirm parallel wiring method and manufacturer guidance |
| Eco-Worthy Cubix 100 battery 2 | 48 V DC bus / battery combiner | 48 V nominal | TBD | TBD | Confirm equal-length cabling and per-battery protection |
| 48 V DC bus | Midnite Solar Classic 200 battery terminals | 48 V nominal | TBD | TBD | Solar charge output to battery bank |
| 48 V DC bus | MagnaSine 4448 DC input | 48 V nominal | TBD | TBD | High-current inverter feed; document disconnect and overcurrent protection |
| 48 V DC bus | Victron Orion 48/12 DC-DC converter input | 48 V nominal | TBD | TBD DC fuse/breaker | Supplies 12 V control bus; confirm exact Orion model and input fuse size |
| Victron Orion 12 V output | 12 V control bus | 12 V nominal | TBD | TBD DC fuse/breaker | Feeds thermostat, relay/driver boards, SSR control, fan/damper, and possible powered USB hub |
| 48 V DC bus | Battery heater mat pair | 48 V nominal | TBD | 10 A DC breaker / manual disconnect | Through DC SSR and one-shot thermal fuse; heater is about 200 W / 4.2 A |

## Signal Wiring

| From | To | Signal Type | Cable / Connector | Notes |
|---|---|---|---|---|
| Raspberry Pi USB | Eco-Worthy Cubix 100 CAN port | CAN | DSD TECH SH-C31G, Cat-6 cable, RJ45 battery-side plug | Battery RJ45 pin 4 CANH1, pin 5 CANL1, pin 3 or 6 GND |
| Raspberry Pi USB | Eco-Worthy Cubix 100 RS485-1 port | RS485 | Waveshare isolated USB-RS485/422, Cat-6 cable, RJ45 battery-side plug | Battery RJ45 pins 1/8 B1, pins 2/7 A1, pins 3/6 GND |
| Raspberry Pi | Midnite Solar Classic 200 | TBD | TBD | Confirm supported telemetry path before wiring |
| Raspberry Pi | MagnaSine 4448 | TBD | TBD | Confirm Magnum interface accessory requirements |
| Raspberry Pi GPIO4 | DS18B20 temperature bus | 1-Wire | 3-conductor low-voltage cable | 4.7k pull-up from DATA to 3.3 V; map sensor IDs to physical locations |
| Raspberry Pi GPIO | Optocoupler isolation board | Digital output | Low-voltage control wiring | Pi heater permissive; fail-off when Pi/GPIO is inactive |
| 12 V control rail | Heater SSR input | Digital control | Thermostat, NC thermal switch, Pi permissive | Thermostat and Pi switch SSR input only, not heater current |
| 12 V control rail | Vent fan / damper | Digital control | Thermostat cooling relay | Fan/ventilation path for warm enclosure conditions |

## Labels

Use durable labels on both ends of each cable. Match physical labels to the names used in this repo.

| Label | Location | Meaning |
|---|---|---|
| BAT-1 | Battery rack | Eco-Worthy Cubix 100 battery 1 |
| BAT-2 | Battery rack | Eco-Worthy Cubix 100 battery 2 |
| BAT-CAN | Battery CAN port and controller enclosure | Battery CAN telemetry cable |
| BAT-RS485-1 | Battery RS485-1 port and controller enclosure | Battery RS485 fallback telemetry cable |
| CC-1 | Charge controller area | Midnite Solar Classic 200 |
| INV-1 | Inverter area | MagnaSine 4448 |
| PI-1 | Control enclosure | Raspberry Pi supervisory controller |

## Photos

Place installation photos in `photos/wiring/` and reference them here.
