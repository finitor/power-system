# Telemetry Topics

Use stable telemetry names and include units in documentation, not in the topic name.

These names are transport-neutral for now. They could become MQTT topics, database measurement names, API field names, or dashboard metric IDs later.

| Topic | Payload | Unit | Notes |
|---|---|---:|---|
| `power/battery_bank/voltage` | number | V | Battery bank terminal voltage |
| `power/battery_bank/current` | number | A | Positive for charging, negative for discharging |
| `power/battery_bank/soc` | number | % | Battery bank state of charge |
| `power/battery_bank/temperature` | number | C | Representative battery temperature |
| `power/battery_bank/battery_1/status` | JSON |  | Eco-Worthy Cubix 100 battery 1 BMS/status payload, shape TBD |
| `power/battery_bank/battery_2/status` | JSON |  | Eco-Worthy Cubix 100 battery 2 BMS/status payload, shape TBD |
| `power/charge_controller/pv_voltage` | number | V | Midnite Solar Classic 200 PV input voltage |
| `power/charge_controller/pv_current` | number | A | Midnite Solar Classic 200 PV input current |
| `power/charge_controller/output_current` | number | A | Charge current into battery bus |
| `power/charge_controller/state` | string |  | Charge stage or operating state |
| `power/inverter_charger/dc_voltage` | number | V | MagnaSine 4448 DC input voltage |
| `power/inverter_charger/ac_output_power` | number | W | AC output load |
| `power/inverter_charger/ac_input_state` | string |  | AC input / generator / grid availability, if monitored |
| `power/inverter_charger/state` | string |  | Inverting, charging, standby, fault, or other supported state |
| `power/battery_temperature/heater_state` | string |  | Off, heating, inhibited, fault |
| `power/battery_temperature/heater_power` | number | W | Estimated or measured heater power |
| `power/battery_temperature/enclosure_temperature` | number | C | Battery enclosure air temperature |
| `power/battery_temperature/battery_temperature_min` | number | C | Minimum observed battery temperature |
| `power/battery_temperature/heater_plate_temperature` | number | C | Heater spreader plate temperature |
| `power/battery_temperature/vent_state` | string |  | Closed, open, fan_on, fault |
| `power/ambient/temperature` | number | C | Utility-room ambient air temperature from AM2302/DHT22 |
| `power/ambient/humidity` | number | % | Utility-room ambient relative humidity from AM2302/DHT22 |
| `power/system/health` | JSON |  | Service health summary |
