# Shopping List

Procurement status values:

- `to order`: needed or likely needed, not yet purchased.
- `ordered`: purchased but not yet on hand.
- `on hand`: physically available at the site or bench.

## Current View

| Status | Component | Spec / Model | Qty | For | Notes |
|---|---|---|---:|---|---|
| on hand | Raspberry Pi | 3 Model B v1.2 | 1 | Supervisor | Existing controller/logger |
| on hand | Powered USB hub | Waveshare USB 3.2-Gen1-HUB-4U | 1 | Supervisor USB expansion | Candidate powered hub for Pi telemetry adapters and bench/service USB |
| on hand | USB-to-CAN adapter | DSD TECH SH-C31G | 1 | Battery CAN telemetry | Flashed to CANable/Candlelight-compatible firmware |
| on hand | RJ45 screw-terminal breakouts | Poyiccot RJ45 8P8C female to screw terminal | 2 | Battery comms bench wiring | Used for CAN/RS485 probing |
| on hand | Rack batteries | Eco-Worthy Cubix 100, 48 V 100 Ah | 2 | Battery bank | Installed as 200 Ah nominal LiFePO4 bank |
| on hand | Battery remote monitor | Eco-Worthy ESM-100 | 1 | Battery status display | BMS voltage/current/SOC view |
| on hand | Charge controller | Midnite Solar Classic 200 | 1 | Existing PV source / array 0 | Candidate controller for high-voltage array 1 |
| on hand | PV array 0 modules | Canadian Solar CS6X-300-adjacent panels | 8 | Existing array | Currently documented as 4s2p |
| on hand | PV array 1 modules | Canadian Solar CS6X-300-adjacent panels | 12 | New array dry run | Currently dry-run as 4s3p before mount construction |
| on hand | Inverter/charger | MagnaSine 4448 | 1 | AC inversion / generator charging | Existing inverter/charger |
| on hand | Temperature sensors | DS18B20 stainless waterproof probes | 10 | Battery cabinet / ambient temperature | Map sensor IDs during installation |
| on hand | GPIO cobbler / ribbon breakout | Generic 40-pin Pi breakout | 1 | Bench prototyping | Pin breakout only |
| ordered | Charge controller | Victron BlueSolar MPPT 150/85 CAN-bus | 1 | Second PV source | Order appears in limbo; manual says 150 V absolute PV limit; avoid 4s CS6X strings |
| ordered | Charge controller | EPEver TEP10425 | 1 | Candidate replacement/second solar controller | 100 A, 250 V max PV Voc at lowest temperature, 5,200 W at 48 V; manual saved as `~/Dropbox/manuals/solar/TEP-Manual-EN-V1.1.pdf`; bench-confirm writable RS485 Modbus registers and PV-input behavior |
| ordered | PV array disconnect | Walfront Solar DC miniature circuit breaker, 1000 V 50 A, 2-pole DIN | 1 | Candidate combined PV disconnect | Confirm DC load-break suitability, polarity requirements, enclosure/dead-front fit, and conductor ampacity |
| ordered | Charge-controller battery breaker | MOLLOM DC 1-pole miniature circuit breaker, 250 V 100 A | 1 | Candidate controller-to-battery disconnect/protection | Confirm DC interrupt rating, load-break suitability, conductor ampacity, enclosure/dead-front fit, and final output current limit |
| to order | PV string protection | 15 A DC PV-rated fuse/breaker, 300-600 VDC | 3 | Array 1 4s3p string protection | One per string positive before paralleling; panel max series fuse rating is 15 A |
| to order | PV array disconnect | 45-50 A DC PV-rated breaker/disconnect, 300-600 VDC | 1 | Array 1 combined PV output | Design target retained until the ordered Walfront part is verified |
| to order | Charge-controller battery breaker | 100 A DC breaker, 80/125 VDC or better | 1 | Classic or EPEver battery-side protection | Design target retained until the ordered MOLLOM part is verified |
| to order | Charge-controller battery breaker | 120-125 A DC breaker/fuse, 80/125 VDC or better | 1 | Victron 150/85 battery-side protection | Victron manual examples use 120 A for 85 A output; 125 A is common in North America |
| to order | Outdoor PV combiner | 3-string combiner rated for selected DC voltage/current | 1 | Array 1 | Should accept selected string fuses/breakers and conductor sizes |
| to order | Battery USB-to-RS485 adapter | Isolated USB-to-RS485/422, Waveshare candidate | 1 | Battery RS485 fallback | Keep as fallback if CAN telemetry is insufficient |
| to order | Magnum USB-to-RS485 adapter | TBD isolated adapter / RJ11 breakout path | 1 | Magnum network pilot | Keep physically separate from battery RS485 |
| to order | 12 V DC-DC converter | Victron Orion-Tr 48/12-9A isolated candidate | 1 | 12 V control bus | For thermostat, relays, SSR control, fan/damper, possible powered USB hub |
| to order | Battery heater | 48 V 200 W ceramic heater | 1 | Winter battery preheat | Include breaker/manual disconnect and thermal safety chain |
| to order | Heater switching | DC SSR or contactor rated for 48 V heater load | 1 | Battery temperature control | Choose fail-safe off behavior |
| to order | Thermal safety devices | NC thermal switch and one-shot thermal fuse | 1 set | Battery heater safety | Hardware safety independent of Pi |
| to order | Relay board / contactors | TBD | TBD | Controlled outputs | Document fail-safe state before final selection |

## Notes

- Use [hardware inventory](hardware-inventory.md) for full subsystem context and device notes.
- Use [charge controller](subsystems/charge-controller.md) for PV-source planning and controller behavior.
- Breaker/fuse entries are design targets, not final code approval. Final parts must match enclosure, conductor insulation/temp rating, grounding scheme, and applicable electrical code.
