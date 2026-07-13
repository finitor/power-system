# Individual Load Metering

## Installed hardware

| Load | Meter | Address | Supervisor key | Status |
|---|---|---|---|---|
| Refrigeration | Sonoff S31, Tasmota firmware, module 41 | `192.168.0.210` | `refrigeration` | Installed and logging |

The S31 sits between the inverter-fed receptacle and the appliance. It measures
the branch load on the AC side; it is not the same as the inverter's total-load
estimate and must not be added to that total as if it were an additional load.

## Data path

The supervisor reads Tasmota's local HTTP command endpoint with `Status 10`.
This is a read-only integration: it does not use the S31 relay-control commands.
Each configured device runs on the same five-second actor polling cadence as
the primary supervisor devices, while the metric recorder writes the latest
values to SQLite every 60 seconds.

For device key `<name>`:

- live snapshot: `monitored_loads[]`, selected by its `name` field;
- durable metric source: `tasmota.<name>`;
- stored metrics: voltage, current, real/apparent/reactive power, power factor,
  and today/yesterday/lifetime energy;
- rolling average: three hours of real-power samples, with startup history
  restored from the durable metric store.

Refrigeration has its own row at the bottom of the Load group on the terminal,
API terminal, browser, and Kindle displays:

```text
Refrigeration          Now 87W  3hr 100W  Cumulative 0.5kWh
```

Now and 3hr show watts; Cumulative shows Tasmota's local-day kWh counter. This
row is an individual-load subset of the whole-system load, not a second
independent total.

The row is live-only. If the current S31 poll fails, the supervisor omits the
meter from its snapshot and all displays hide the Refrigeration row rather than
showing cached power as though it were current. Existing SQLite history is not
deleted, and the row returns automatically after the next successful poll.

## Configuration boundary

The device registry is the comma-separated `TASMOTA_DEVICES` value in
`/etc/offgrid-power.env`, for example:

```text
TASMOTA_DEVICES=refrigeration=192.168.0.210,freezer=192.168.0.211
```

Registration is generic and supports multiple monitors. Display annotations
are currently specific to the key `refrigeration`; future devices are logged
and exposed by the API immediately, even if they are not yet rendered in the
Load group.

See [Adding a Tasmota Sonoff S31](../runbooks/add-tasmota-s31.md) for the full
device, timezone, supervisor, verification, and troubleshooting procedure.

## Time and energy-counter limitations

Tasmota owns `Today`, `Yesterday`, and `Total`. `Today` resets automatically at
the S31's local midnight and can be reset manually with `EnergyToday 0`. The
supervisor records what the device reports; it does not synthesize or reset the
counter.

The S31 has no durable RTC and normally sets its clock from Internet NTP. Its
Eastern timezone/DST rules are configured locally, but after a simultaneous
power and WAN outage the daily boundary is not trustworthy until the S31 has
time again. Historical supervisor samples remain UTC-stamped by the Pi and can
still be integrated independently if necessary.

## Control and safety boundary

The supervisor deliberately treats each S31 as a meter, not an actuator. The
appliance's required restart behavior after an outage must be configured and
tested at the Tasmota/device layer. Keep the S31 and its HTTP API on the trusted
site LAN; `SetOption128 1` makes ordinary local HTTP requests possible and is
not suitable for a directly Internet-exposed device.
