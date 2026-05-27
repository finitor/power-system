# Hardware Inventory

Track every major physical component here. Prefer exact model numbers and interface details.

| Component | Model | Voltage | Interface | Purpose | Notes |
|---|---|---:|---|---|---|
| Raspberry Pi | 3 Model B v1.2 | 5 V | GPIO / USB / Ethernet / Wi-Fi / Bluetooth | Controller and logger | 1 GB RAM, 4x USB 2.0, 40-pin GPIO; add power supply |
| USB-to-CAN adapter | DSD TECH SH-C31G | USB powered | USB Type-B / CAN terminal block | Preferred battery CAN interface | Isolated cabled adapter based on CANable 2.0; Cubix CAN RJ45 pinout is pin 4 CANH1, pin 5 CANL1, pins 3/6 GND |
| USB-to-RS485 adapter | Waveshare USB TO RS485/422 isolated converter | USB powered | USB / RS485 screw terminal | Fallback battery RS485 interface | Candidate fallback if CAN does not expose required telemetry |
| USB-to-RS485 adapter | TBD isolated adapter | USB powered | USB / RJ11 breakout | Magnum inverter network pilot | Dedicated adapter for Magnum RS485 experiments; keep separate from battery RS485 |
| Rack battery | Eco-Worthy Cubix 100 | 48 V, 100 Ah | Bluetooth/Wi-Fi, RS485, CAN, RS232 | Energy storage | Quantity 2; LiFePO4; built-in 100 A BMS; document BMS ports, protection, and parallel wiring |
| Battery bank | 2x Eco-Worthy Cubix 100 | 48 V nominal, 200 Ah total | BMS / ESM-100 / CAN candidate / RS485 fallback | Energy storage | Confirm usable capacity, charge/discharge limits, and communication options |
| Battery remote monitor | Eco-Worthy ESM-100 | Battery accessory powered | Battery monitor link | Battery voltage/current/SOC display | Makes WhizBang Jr shunt optional for first build if equivalent BMS data is available to Pi |
| Charge controller | Midnite Solar Classic 200 | 48 V battery system, PV input TBD | TBD, likely network / Modbus-capable | Solar charging | Legacy controller; confirm firmware, network access, and telemetry interface |
| Inverter/charger | MagnaSine 4448 | 48 V DC, AC output TBD | TBD, likely Magnum remote / network accessories | AC inversion and charging | Confirm exact model label, continuous rating, AC wiring, and monitoring interface |
| Battery monitor / shunt | WhizBang Jr candidate | TBD | Midnite Classic accessory / shunt | Optional independent current/SOC cross-check | Defer unless BMS/ESM-100 telemetry is incomplete or Classic charge control benefits from shunt data |
| Temperature sensors | TBD | 3.3 V / 5 V | TBD | Battery / enclosure temperature | Add mounting locations |
| Battery temperature control heater | AliExpress V19 fused silicone mat pair, candidate | 48 V / 200 W | Thermostat + Pi permissive + DC SSR | Winter battery preheat | Pair is wired in series; include DC breaker/manual disconnect, NC thermal switch, and one-shot thermal fuse |
| Relay board / contactors | TBD | TBD | GPIO / driver | Controlled outputs | Document fail-safe state |

## Device Addresses

| Device | Bus | Address / Port | Notes |
|---|---|---|---|
| DSD TECH SH-C31G | USB / CAN | CAN RJ45 pins 4/5/3/6 via adapter cable | Candidate Pi CAN interface; confirm firmware mode and Linux device name |
| Waveshare USB TO RS485/422 | USB / RS485 | RS485-1 RJ45 pins 1/8 B1, 2/7 A1, 3/6 GND via adapter cable | Fallback Pi RS485 interface |
| Magnum network RS485 adapter | USB / RS485 | RJ11 pin 1 Data+, pin 4 Data- per `magnum-pi` prior art | Listen-only first; do not transmit commands until passive decoding is proven |
| Eco-Worthy Cubix 100 battery 1 | CAN / RS485 TBD | TBD | Confirm BMS communication options |
| Eco-Worthy Cubix 100 battery 2 | CAN / RS485 TBD | TBD | Confirm whether batteries expose individual telemetry |
| Midnite Solar Classic 200 | TBD | TBD | Confirm Ethernet, Modbus, or other supported interface |
| MagnaSine 4448 | TBD | TBD | Confirm remote/network bridge availability |
