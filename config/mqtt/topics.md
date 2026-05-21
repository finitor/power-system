# MQTT Topics

Use stable topic names and include units in documentation, not in the topic name.

| Topic | Payload | Unit | Notes |
|---|---|---:|---|
| `power/battery/voltage` | number | V | Battery terminal voltage |
| `power/battery/current` | number | A | Positive for charging, negative for discharging |
| `power/battery/soc` | number | % | Battery state of charge |
| `power/battery/temperature` | number | C | Battery temperature |
| `power/system/health` | JSON |  | Service health summary |

