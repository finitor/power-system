# Hardware Inventory

Track every major physical component here. Prefer exact model numbers and interface details.

Use [Shopping List](shopping-list.md) for the one-view procurement status across `to order`, `ordered`, and `on hand`.

| Component | Model | Voltage | Interface | Purpose | Notes |
|---|---|---:|---|---|---|
| Raspberry Pi | 3 Model B v1.2 | 5 V | GPIO / USB / Ethernet / Wi-Fi / Bluetooth | Controller and logger | 1 GB RAM, 4x USB 2.0, 40-pin GPIO; add power supply |
| Powered USB hub | Waveshare USB 3.2-Gen1-HUB-4U | TBD external power / USB bus | USB 3.2 Gen 1 upstream / 4 USB downstream ports | Pi USB expansion | On hand; candidate powered hub for telemetry adapters and bench/service USB expansion |
| 12 V DC-DC converter | Victron Orion-Tr 48/12-9A isolated candidate | 48 V input, 12 V output | 48 V bus to 12 V control bus | Control power | Primary 12 V rail for thermostat, relay/driver boards, SSR control input, fan/damper, and possible powered USB hub input; confirm exact model |
| USB-to-CAN adapter | DSD TECH SH-C31G | USB powered | USB Type-B / CAN terminal block | Preferred battery CAN interface | Isolated cabled adapter based on CANable 2.0; Cubix CAN RJ45 pinout is pin 4 CANH1, pin 5 CANL1, pins 3/6 GND |
| Battery USB-to-RS485 adapter | Waveshare USB TO RS485/422 isolated converter | USB powered | USB / RS485 screw terminal | Fallback battery RS485 interface | Candidate fallback if CAN does not expose required Cubix telemetry |
| RJ45 female screw-terminal breakouts | 2x Poyiccot RJ45 8P8C female jack to 8-pin screw terminal | RJ45 low-voltage comms | RJ45 female / screw terminal | Battery comms bench wiring | On hand; use for adapter-side breakout and probing of Eco-Worthy battery CAN/RS485 instrumentation |
| Magnum USB-to-RS485 adapter | Waveshare USB TO RS485/422 Industrial Grade Isolated Converter | USB powered | USB / RJ11 breakout | Magnum inverter network pilot | On order, expected 2026-06-05; onboard original FT232RL and SP485EEN with multiple protection; keep physically separate from the battery RS485 bus |
| Rack batteries | 2x Eco-Worthy Cubix 100 | 48 V, 100 Ah each; 200 Ah total nominal bank | Bluetooth/Wi-Fi, RS485, CAN, RS232, ESM-100 monitor | Energy storage | LiFePO4; built-in 100 A BMS per battery; each battery is 56.1 x 48.3 x 14.3 cm / 22.09 x 19.02 x 5.63 in; confirm usable capacity, charge/discharge limits, and parallel wiring |
| Battery remote monitor | Eco-Worthy ESM-100 | Battery accessory powered | Battery monitor link | Battery voltage/current/SOC display | Makes WhizBang Jr shunt optional for first build if equivalent BMS data is available to Pi |
| Charge controller | Midnite Solar Classic 200 | 48 V battery system, PV input TBD | TBD, likely network / Modbus-capable | Solar charging | Legacy controller; confirm firmware, network access, and telemetry interface |
| Charge controller | Victron BlueSolar MPPT 150/85 CAN-bus | 48 V battery system, 150 V PV input, 85 A output | VE.Can / RJ45 CAN bus | Second PV array solar charging | Ordered; manual identifies two RJ45 CAN connectors, VE.Can parallel operation, and NMEA2000 protocol |
| Charge controller | EPEver TEP10425 | 12/24/48 V battery system, 250 V max PV Voc at lowest temperature, 225 V max PV Voc at 25 C, 100 A charge output, 5,200 W at 48 V | RS485 Modbus RJ45, optional Wi-Fi/Bluetooth/TCP/4G modules, BMS/CAN COM port | Candidate replacement/second solar controller | Ordered; manual at `~/Dropbox/manuals/solar/TEP-Manual-EN-V1.1.pdf`; manual says TEP10425 supports two PV inputs, IP20, common-negative grounding, local parameter setting, RS485 Modbus, built-in BMS communication port, and built-in CAN parallel communication port; bench-confirm writable registers and PV-input behavior before relying on it |
| PV array 0 | Canadian Solar CS6X-300-adjacent modules | Nominal module rating about 295-305 W each | 4s2p PV string wiring | Existing solar input | On hand / installed; 8 modules total; exact per-module ratings may vary across CS6X-300, CS6X-295, or CS6X-305 |
| PV array 1 | Canadian Solar CS6X-300-adjacent modules | Nominal module rating about 295-305 W each | 4s3p PV string wiring | Second solar input | On hand; currently in dry run on the ground before mount construction; 12 modules total; exact per-module ratings may vary across CS6X-300, CS6X-295, or CS6X-305 |
| PV array 0 disconnect | Existing 2-pole disconnect / breaker | 20 A, DC rating TBD | PV array 0 to Midnite Solar Classic 200 PV input | Existing array-side disconnect | Confirm manufacturer/model, DC/PV voltage rating, load-break suitability, enclosure, conductor gauge, and whether it remains usable after any array reconfiguration |
| Classic battery disconnect | Existing 2-pole disconnect / breaker | 100 A, DC rating TBD | Midnite Solar Classic 200 battery output to 48 V bus | Existing controller battery-side disconnect | Confirm manufacturer/model, DC voltage rating, polarity/wiring, enclosure, conductor gauge, and interrupt rating |
| PV array disconnect | Walfront Solar DC Miniature Circuit Breaker | 1000 VDC, 50 A, 2-pole | DIN rail | Candidate PV combined disconnect | Ordered; intended for PV-side disconnect duty. Confirm load-break suitability, polarity requirements, enclosure/dead-front arrangement, conductor ampacity, and final array current before installation |
| Charge-controller battery breaker | MOLLOM DC 1-pole miniature circuit breaker | 250 VDC, 100 A, 1-pole | DIN rail | Candidate charge-controller output breaker/disconnect | Ordered; likely candidate for controller-to-battery positive. Confirm DC interrupt rating, load-break/disconnect suitability, conductor ampacity, enclosure/dead-front arrangement, and whether 100 A is acceptable for the final controller output limit |
| Inverter/charger | MagnaSine 4448 | 48 V DC, AC output TBD | TBD, likely Magnum remote / network accessories | AC inversion and charging | Confirm exact model label, continuous rating, AC wiring, and monitoring interface |
| Battery monitor / shunt | WhizBang Jr candidate | TBD | Midnite Classic accessory / shunt | Optional independent current/SOC cross-check | Defer unless BMS/ESM-100 telemetry is incomplete or Classic charge control benefits from shunt data |
| Temperature sensors | 10x DS18B20 stainless waterproof probes | 3.3 V | 1-Wire GPIO | Battery / enclosure temperature | Map each sensor ID to physical location during installation |
| Battery temperature control heater | 48 V 200 W ceramic heater | 48 V / 200 W | Thermostat + Pi permissive + DC SSR | Winter battery preheat | Substitute for the previously considered AliExpress V19 fused silicone heating pad pair; include DC breaker/manual disconnect, NC thermal switch, and one-shot thermal fuse |
| Raspberry Pi GPIO cobbler / ribbon breakout | Generic GPIO breakout board | 3.3 V / 5 V GPIO rails | 40-pin Pi ribbon to breadboard | Bench prototyping | Pin breakout only; does not include DHT22/AM2302 DATA pull-up resistor |
| Relay board / contactors | TBD | TBD | GPIO / driver | Controlled outputs | Document fail-safe state |

## Device Addresses

| Device | Bus | Address / Port | Notes |
|---|---|---|---|
| DSD TECH SH-C31G | USB / CAN | CAN RJ45 pins 4/5/3/6 via adapter cable | Candidate Pi CAN interface; confirm firmware mode and Linux device name |
| Waveshare USB TO RS485/422 | USB / RS485 | RS485-1 RJ45 pins 1/8 B1, 2/7 A1, 3/6 GND via adapter cable | Fallback Pi RS485 interface |
| Magnum network RS485 adapter | USB / RS485 | RJ45 breakout pin 6 to adapter A/D+, pin 3 to adapter B/D-; leave pin 4 +14 V and pin 5 GND disconnected from adapter | Listen-only first; bench measurements showed 14.14 V across pins 4/5 and about 3.3 V differential from pin 6 to pin 3 |
| Eco-Worthy Cubix 100 battery 1 | CAN / RS485 TBD | TBD | Confirm BMS communication options |
| Eco-Worthy Cubix 100 battery 2 | CAN / RS485 TBD | TBD | Confirm whether batteries expose individual telemetry |
| Midnite Solar Classic 200 | TBD | TBD | Confirm Ethernet, Modbus, or other supported interface |
| Victron BlueSolar MPPT 150/85 CAN-bus | VE.Can / RJ45 CAN bus | TBD | Ordered second charge controller; likely separate PV source telemetry path from Classic |
| EPEver TEP10425 | RS485 Modbus / RJ45, BMS/CAN COM | TBD | Ordered; use isolated USB-RS485 or a Modbus RTU-to-TCP gateway for first writable-register tests |
| MagnaSine 4448 | TBD | TBD | Confirm remote/network bridge availability |
