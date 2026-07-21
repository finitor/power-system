# Individual Load Metering

## Installed hardware

| Load | Meter | Address | Supervisor key | Status |
|---|---|---|---|---|
| Refrigeration | Sonoff S31, Tasmota firmware, module 41 | `192.168.0.210` | `refrigeration` | Installed and logging |

The S31 sits between the inverter-fed receptacle and the appliance. It measures
the branch load on the AC side; it is not the same as the inverter's total-load
estimate and must not be added to that total as if it were an additional load.
The metered branch supplies two nominally identical cube freezers: one operates
as a deep freezer and the other is switched by an external thermostat at
refrigerator temperature. There is no cabinet-temperature telemetry.

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

## Measured refrigeration utilization (2026-07-10 through 2026-07-20)

The combined power trace has four clean steady-state tiers, so the two
compressors can be separated without temperature sensors:

| Combined state | Observed power | Share of covered time |
|---|---:|---:|
| Neither compressor | ~1 W | 50.0% |
| Lower-power compressor only | 50–56 W | 40.5% |
| Higher-power compressor only | 70–75 W | 5.4% |
| Both compressors | 115–130 W | 4.1% |

The analysis used 13,533 one-minute samples spanning 233.6 covered hours
(local 2026-07-10 19:06 through 2026-07-20 12:56). Only three gaps exceeded
90 seconds, totaling 0.18 hours. Thresholds were placed in the empty valleys
between the tiers: idle `<25 W`, lower-only `25–<63 W`, higher-only `63–<95 W`,
and both `95–<150 W`. The 32 readings at or above 150 W (0.24% of samples)
were compressor-start transients and were assigned from the adjacent stable
state.

For covered time `T`, lower-only time `L`, higher-only time `H`, and both-on
time `B`, the inferred unit duties are `(L+B)/T` and `(H+B)/T`. The system's
two-compressor capacity-normalized duty is `(L+H+2B)/(2T)`.

| Quantity | Whole-window result | Median complete day | Complete-day range |
|---|---:|---:|---:|
| Lower-power compressor duty | 44.6% | 45.4% | 41.6–46.3% |
| Higher-power compressor duty | 9.5% | 9.4% | 8.4–10.8% |
| Capacity-normalized system duty | 27.1% | 27.7% | 25.0–28.1% |

At least one compressor ran 50.0% of wall time, but the system averaged only
0.54 running compressors because most active time involved one unit. Median
cycles were about 9.3 minutes on / 11.5 minutes off for the lower-power unit
and 3.1 minutes on / 27 minutes off for the higher-power unit. Cycle lengths
are quantized by the one-minute durable sampling cadence.

The meter cannot prove which physical appliance owns a tier. The natural
assignment is the 50–56 W, high-duty unit as the deep freezer and the 70–75 W,
low-duty unit as the thermostat-converted refrigerator. The warmer evaporator
in refrigerator service raises suction density and refrigerant mass flow,
which can increase running power while sharply reducing required runtime. A
one-unit-at-a-time test would confirm the mapping.

Mean branch power was 32.1 W, equivalent to about **0.77 kWh/day** under the
conditions observed. This replaces the earlier pre-meter estimate of
~2.6 kWh/day, but it is a summer measurement and should not be assumed to hold
through different ambient temperatures, loading, lid-opening patterns, or
thermostat settings. The refrigerator's short runs may also reflect a probe
responding mainly to air temperature; coupling the probe to added thermal mass
would be expected to lengthen cycles, not materially change the steady heat
load.

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
