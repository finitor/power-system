# Adding a Tasmota Sonoff S31 Energy Monitor

This procedure adds a Tasmota-flashed Sonoff S31 as a read-only, individually
metered load in the off-grid supervisor. The installed example is the
refrigeration monitor at `192.168.0.210`.

## 1. Prepare and identify the S31

Flash a current Tasmota build, join the site Wi-Fi, and reserve a stable IPv4
address in DHCP. In **Configuration > Configure Module**, select **Sonoff S31
(41)** and allow the device to reboot.

Open the Tasmota console and set unique names. Replace the example values for
each new load; use a lower-case, punctuation-free hostname.

```text
Backlog DeviceName Refrigeration; FriendlyName1 Refrigeration; Hostname s31-refrigeration
```

The supervisor uses its own lower-case key from `TASMOTA_DEVICES`; it does not
derive that key from `DeviceName` or `FriendlyName1`.

## 2. Permit the local read API

The supervisor only sends `Status 10` reads, but Tasmota's optional HTTP
Referer protection can reject otherwise ordinary clients. Disable that check
on this trusted local device:

```text
SetOption128 1
```

The reader also supplies a same-device `Referer` for compatibility. Setting
`SetOption128 1` is still preferred because it leaves the local HTTP API usable
by `curl`, browsers, and other normal LAN clients. Do not expose the Tasmota web
UI or `/cm` endpoint to the public Internet.

Set the device telemetry period to one minute:

```text
TelePeriod 60
```

`TelePeriod` controls Tasmota's own periodic telemetry. The supervisor's HTTP
poll remains independent: it reads every 5 seconds and persists snapshots once
per minute.

## 3. Set site-local time

Daily energy is a Tasmota-maintained counter that rolls over at the device's
local midnight. Configure the Wawa/Toronto Eastern time rules:

```text
Backlog Timezone 99; TimeStd 0,1,11,1,2,-300; TimeDst 0,2,3,1,2,-240
```

Tasmota obtains wall time from its configured NTP servers; the S31 has no
battery-backed RTC. Once synchronized, it keeps time while powered. Following a
site-wide power cycle with no WAN, its `Today`/`Yesterday` boundary may be wrong
until NTP becomes reachable. The Pi's Classic-based boot clock recovery does
not currently provide NTP to the S31.

Tasmota resets `ENERGY.Today` automatically at local midnight. A manual reset,
when deliberately required, is:

```text
EnergyToday 0
```

## 4. Verify the S31 directly

From the Pi or another machine on the site LAN (no `jq` required):

```sh
curl -fsS 'http://192.168.0.210/cm?cmnd=Status%2010' | python3 -m json.tool
```

Confirm that `StatusSNS.ENERGY` contains plausible `Voltage`, `Current`,
`Power`, `Today`, `Yesterday`, and `Total` values. Verify the relay is on and
choose its power-loss behavior deliberately in Tasmota; the supervisor never
switches the outlet.

## 5. Register it with the supervisor

On the Pi, add the stable address to `/etc/offgrid-power.env`. Preserve existing
entries and separate devices with commas:

```text
TASMOTA_DEVICES=refrigeration=192.168.0.210,freezer=192.168.0.211
```

Keys must be unique. Use lower-case stable identifiers because each key becomes
part of the durable metric source (`tasmota.<key>`). Then restart and check the
service:

```sh
sudo systemctl restart offgrid-supervisor
systemctl is-active offgrid-supervisor
curl -fsS http://127.0.0.1:8081/api/v1/snapshot |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["monitored_loads"])'
```

Every registered device is automatically:

- polled over local HTTP every 5 seconds;
- exposed in `GET /api/v1/snapshot` under `monitored_loads`;
- persisted every 60 seconds under source `tasmota.<key>`;
- given a three-hour rolling power average, re-seeded from SQLite after a
  supervisor restart.

The current parenthetical display annotations are specifically keyed to
`refrigeration`. A newly registered device is readable and loggable without a
code change, but adding it to the Load lines on every display requires extending
the display renderers and their tests.

## 6. Verify durable logging

After at least one minute:

```sh
sqlite3 'file:/srv/telemetry/data/metrics.sqlite?mode=ro' \
  "SELECT captured_at, metric, value, unit FROM samples WHERE source='tasmota.refrigeration' ORDER BY captured_at DESC LIMIT 12;"
```

Expected metrics are `voltage`, `current`, `power`, `apparent_power`,
`reactive_power`, `power_factor`, `daily_energy`, `yesterday_energy`, and
`lifetime_energy`.

## Troubleshooting

- HTTP 403 or an empty browser response: confirm `SetOption128 1`, then retry
  `Status 10` directly.
- Connection refused/time-out: verify the DHCP reservation, Wi-Fi association,
  and IP address from the Tasmota Information page.
- `Tasmota <key> read failed` in supervisor status: compare the direct `curl`
  response with `journalctl -u offgrid-supervisor -n 50 --no-pager`.
- Wrong daily rollover: check `Status 7`/the Tasmota Information page for local
  time and reapply the timezone rules. Confirm WAN/NTP availability after a
  full power loss.
- New device missing only from the display: check `monitored_loads` and SQLite;
  display inclusion is separate from registration, as described above.
