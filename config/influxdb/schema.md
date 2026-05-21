# InfluxDB Schema

Proposed bucket: `power`

## Measurements

| Measurement | Tags | Fields | Notes |
|---|---|---|---|
| `battery` | `device`, `location` | `voltage`, `current`, `soc`, `temperature` | Core storage state |
| `solar` | `device`, `array` | `voltage`, `current`, `power` | Generation |
| `inverter` | `device` | `state`, `ac_power`, `temperature` | AC conversion |
| `system` | `service`, `host` | `healthy`, `uptime_seconds` | Pi services |

