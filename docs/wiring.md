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
| Eco-Worthy Cubix 100 battery 1 | 48 V DC bus / battery combiner | 48 V nominal | TBD | TBD | Parallel bank is wired diagonally/cross-connected: main positive is taken from one battery end of the parallel set and main negative from the opposite battery end. This was corrected on 2026-06-03 to improve current sharing and reduce pack drift. Confirm cable gauge, equal-length interconnects, torque, and per-battery protection. |
| Eco-Worthy Cubix 100 battery 2 | 48 V DC bus / battery combiner | 48 V nominal | TBD | TBD | Parallel bank is wired diagonally/cross-connected with the opposite main takeoff from battery 1. Do not connect both main positive and main negative to the same battery in normal operation. Confirm manufacturer guidance and document final cable lengths. |
| PV array 0 | Midnite Solar Classic 200 PV input | PV DC | TBD | Existing 20 A 2-pole disconnect | Existing array-side disconnect; confirm DC/PV voltage rating, load-break rating, and conductor ampacity before reconfiguring array 0 |
| 48 V DC bus | Midnite Solar Classic 200 battery terminals | 48 V nominal | TBD | Existing 100 A 2-pole disconnect | Solar charge output to battery bank; confirm DC voltage rating, polarity/wiring, and conductor ampacity |
| 48 V DC bus | MagnaSine 4448 DC input | 48 V nominal | TBD | TBD | High-current inverter feed; document disconnect and overcurrent protection |
| 48 V DC bus | Victron Orion 48/12 DC-DC converter input | 48 V nominal | TBD | TBD DC fuse/breaker | Supplies 12 V control bus; confirm exact Orion model and input fuse size |
| Victron Orion 12 V output | 12 V control bus | 12 V nominal | TBD | TBD DC fuse/breaker | Feeds thermostat, relay/driver boards, SSR control, fan/damper, and possible powered USB hub |
| 48 V DC bus | Battery ceramic heater | 48 V nominal | TBD | 10 A DC breaker / manual disconnect | Through DC SSR and one-shot thermal fuse; heater is about 200 W / 4.2 A |

## Signal Wiring

| From | To | Signal Type | Cable / Connector | Notes |
|---|---|---|---|---|
| Raspberry Pi USB | Eco-Worthy Cubix 100 CAN port | CAN | DSD TECH SH-C31G, Cat-6 cable, RJ45 battery-side plug | Battery RJ45 pin 4 CANH1, pin 5 CANL1, pin 3 or 6 GND |
| Raspberry Pi USB | EPEver TEP10425 port 9 (CAN) | CAN | DSD TECH SH-C31G (shared with Cubix; one adapter on hand) | Port-9 RJ45 pin 4 CAN-H, pin 5 CAN-L, pin 8 GND (manual §1.2.1). CAN pair is the same pins as the Cubix cable; only GND differs (Cubix 3/6 → EPever 8). Port 9 also carries RS485 on pins 3/6. Bench-only CAN sniff to test whether Pylon BPRO=21 rides CAN; see [epever research notes](research/epever-tep10425.md). |
| Raspberry Pi USB | Eco-Worthy Cubix 100 RS485-1 port | RS485 | Waveshare isolated USB-RS485/422, Cat-6 cable, RJ45 battery-side plug | Battery RJ45 pins 1/8 B1, pins 2/7 A1, pins 3/6 GND |
| Raspberry Pi USB | MagnaSine 4448 network port | RS485 | Waveshare isolated USB-RS485/422, 4-wire RJ11 cable, RJ45 straight-through coupler, Cat-6 patch, RJ45 screw-terminal breakout | Bench-measured breakout mapping: RJ45 pin 4 +14 V, pin 5 GND, pin 6 RS485 A / D+, pin 3 RS485 B / D-. Connect only pin 6 to adapter A/D+ and pin 3 to adapter B/D- for first try; leave pins 4 and 5 disconnected from the USB adapter. |
| Eco-Worthy Cubix 100 RS232 port | Eco-Worthy ESM-100 remote display | RS232 / accessory power | Native short cable is RJ12 6P6C straight-through by wire-color inspection | Do not substitute Midnite Classic remote cable; it is RJ12 6P6C reversed/rolled with pin positions mirrored end-to-end |
| Raspberry Pi | Midnite Solar Classic 200 | TBD | TBD | Confirm supported telemetry path before wiring |
| Raspberry Pi | MagnaSine 4448 | TBD | TBD | Confirm Magnum interface accessory requirements |
| Raspberry Pi GPIO4 | DS18B20 temperature bus | 1-Wire | 3-conductor low-voltage cable | 4.7k pull-up from DATA to 3.3 V; map sensor IDs to physical locations |
| Raspberry Pi GPIO | Optocoupler isolation board | Digital output | Low-voltage control wiring | Pi heater permissive; fail-off when Pi/GPIO is inactive |
| 12 V control rail | Heater SSR input | Digital control | Thermostat, NC thermal switch, Pi permissive | Thermostat and Pi switch SSR input only, not heater current |
| 12 V control rail | Vent fan / damper | Digital control | Thermostat cooling relay | Fan/ventilation path for warm enclosure conditions |

### RJ12 modular-jack breakout (CAN bench tap)

Sacrificial T568B Ethernet patch cable landed on the RJ12 modular-jack
breakout for the port-9 CAN work. First color is the patch-cable conductor,
second is the breakout's flying-lead wire. Signal column is EPever TEP10425
port 9 (pin 4 CAN-H, pin 5 CAN-L, pin 3 RS485-B per manual §1.2.1); CAN-H/CAN-L
are on the same pins for the Cubix CAN port.

| Patch wire (T568B) | RJ45 pin | Breakout wire | Signal (EPever port 9) |
|---|---:|---|---|
| white/orange | 1 | blue | — (unused) |
| orange | 2 | orange | — (unused) |
| white/green | 3 | black | RS485-B |
| blue | 4 | **red** | **CAN-H** |
| white/blue | 5 | **green** | **CAN-L** |
| green | 6 | yellow | RS485-A |
| white/brown | 7 | brown | — (unused) |
| brown | 8 | white | GND |

All eight conductors are broken out. For the CAN tap only two wires matter:
**CAN-H = red, CAN-L = green.** The full RS485 pair is also available
(RS485-B = black, RS485-A = yellow) if the RS485 path is ever revisited, and
GND is on the white wire (pin 8) — unneeded on the isolated SH-C31G for this
run, but there if a ground reference is wanted. Verify color-to-pin continuity
before trusting it.

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
