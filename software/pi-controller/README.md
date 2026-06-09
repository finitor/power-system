# Pi Controller

Read-only supervisory monitoring and future control orchestration for the Raspberry Pi.

## Current Scaffold

- `src/offgrid_power/classic.py`: MidNite Classic Modbus TCP telemetry adapter.
- `src/offgrid_power/ambient.py`: AM2302/DHT22 ambient temperature and humidity adapter.
- `src/offgrid_power/canbus.py`: SocketCAN discovery and battery CAN decoding helpers.
- `src/offgrid_power/supervisor.py`: combines adapter reads into a single snapshot.
- `src/offgrid_power/metrics.py`: append-only SQLite metric storage.
- `src/offgrid_power/r2_export.py`: store-and-forward metric batch export to R2/S3-compatible object storage.
- `src/offgrid_power/api_terminal_display.py`: terminal renderer for supervisor API snapshots.
- `src/offgrid_power/terminal_display.py`: renders a compact terminal status view.
- `src/offgrid_power/web_display.py`: renders and serves primitive HTML status pages.
- `src/offgrid_power/cli/can_decode.py`: live or log-based battery CAN decoder.
- `src/offgrid_power/cli/r2_export.py`: uploads unexported SQLite metric batches to R2.
- `src/offgrid_power/cli/api_terminal_display.py`: read-only terminal display client for `/api/v1/snapshot`.
- `src/offgrid_power/cli/supervisor_display.py`: production entry point for hardware polling, metrics, and web/API serving.
- `src/offgrid_power/cli/web_display.py`: local HTTP display server for wall displays.
- `../../scripts/supervisor-display.py`: compatibility wrapper for local repo runs.

Run from the repo root:

```sh
source .venv/bin/activate
python -m pip install -e .
offgrid-supervisor --classic-host 192.168.0.10
```

Use `--once` for a single snapshot. The current scaffold is read-only and performs no control writes.

Supervisor metrics are appended to `data/metrics.sqlite` by default. On blueberry, mutable telemetry is SSD-backed under `/srv/offgrid`; the production service writes metrics to `/srv/offgrid/data/metrics.sqlite`. To upload unexported metric batches during a WAN window, configure the object-storage environment variables from `.env.example` and run:

```sh
offgrid-r2-export
```

See [Store-And-Forward Metrics](../../docs/telemetry/store-and-forward.md) for the object format and delivery contract.

To serve the same snapshots that the terminal supervisor is rendering over local HTTP:

```sh
offgrid-supervisor --classic-host 192.168.0.10 --web-display --web-port 8080
```

To render the supervisor's latest API snapshot in a terminal without polling hardware or writing metrics:

```sh
offgrid-terminal-display --url http://127.0.0.1:8080/api/v1/snapshot
```

The production supervisor can also serve a Kindle-safe weather page at `/weather`. It uses Open-Meteo server-side, caches the last successful response, and links back to the power display.

The standalone web server is still useful for quick tests:

```sh
offgrid-web-display --host 0.0.0.0 --port 8080 --classic-host 192.168.0.10
```

Open `http://blueberry.local:8080/` from the Kindle browser. The page is plain HTML/CSS, uses a 60 second meta refresh, and performs no control writes. Rendering is selected from the browser user agent; `/kindle` remains as a compatibility alias while testing. Access logs are written to `data/web-display-access.log` by default and to `/srv/offgrid/logs/web-display-access.log` in the production service; try `http://blueberry.local:8080/healthz` when debugging old browsers or Wi-Fi reachability.

To inspect the Eco-Worthy/Pylon-style battery CAN bus from the Pi:

```sh
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 listen-only on
sudo ip link set can0 up
offgrid-can-decode --interface can0 --seconds 3 --raw
```

The production display defaults to the current Pylon-compatible profile. A later switch to the Eco-Worthy app's "Victron" profile can be tested without code changes by setting:

```sh
BATTERY_CAN_PROTOCOL=ecoworthy-victron
```

or by launching:

```sh
offgrid-supervisor --battery-can-protocol ecoworthy-victron
```

The `ecoworthy-victron` profile currently uses the same 500 kbit/s standard-frame decoder as Pylon, based on the May 31, 2026 capture where the core live metrics remained available and the manufacturer field changed to `ECO-LFP4`.

For passive protocol surveys and raw capture logs:

```sh
sudo offgrid-can-survey --interface can0 --bitrates 250000,500000 --seconds 10 --label battery-protocol-check
```

Keep `can0` in listen-only mode while validating telemetry. The CAN decoder currently treats writable/control behavior as unavailable and only decodes battery-to-inverter telemetry and permissive/request frames.

Dynamic charger current tapering is available but off by default. The current actuator is `classic.0`; it writes only the volatile Classic battery-current limit, never EEPROM, and clamps against BMS CCL / charge-enable state:

```sh
CHARGER_CURRENT_TAPER_DRY_RUN=true
CHARGER_CURRENT_TAPER=true
```

Use dry-run first. The policy targets about 100 A below the top knee, 20-30 A through the first ramp-down, 4-10 A near the top, and 0 A when the BMS disables charge, high-cell voltage is unsafe, or the full-charge latch is active.

Install the sensor extra on the Raspberry Pi before reading the AM2302/DHT22 sensor. DS18B20 probes use Linux's 1-Wire interface and do not need this extra, but it is harmless to leave installed.

```sh
python -m pip install -e '.[sensors]'
```

Default ambient temperature sensing uses DS18B20 1-Wire probes on BCM GPIO 4, physical pin 7:

| DS18B20 wire | Raspberry Pi connection |
|---|---|
| Red / VDD | 3.3 V, physical pin 1 or 17 |
| Yellow or white / DATA | GPIO 4, physical pin 7 |
| Black / GND | Ground, physical pin 6 or another GND |

Add a pull-up resistor from DATA to 3.3 V. A 4.7k resistor is typical; two available 10k resistors in parallel make about 5k and are a good substitute. Multiple DS18B20 probes can share the same 3.3 V, DATA, GND, and pull-up.

Enable 1-Wire on the Pi by adding this line to `/boot/firmware/config.txt` on Raspberry Pi OS Bookworm, then rebooting:

```text
dtoverlay=w1-gpio,gpiopin=4
```

After reboot, detected probes appear as directories under `/sys/bus/w1/devices/` with names beginning `28-`.

To gather DS18B20 ambient temperature data in a CSV file:

```sh
offgrid-supervisor \
  --no-classic \
  --ambient-kind ds18b20 \
  --ambient-log-path /home/@OFFGRID_USER@/power-system/data/ambient.csv
```

The old AM2302/DHT22 path is still available for reference. Its default wiring uses BCM GPIO 4, physical pin 7:

| AM2302/DHT22 pin | Raspberry Pi connection |
|---|---|
| VCC / + | 3.3 V, physical pin 1 |
| DATA / OUT | GPIO 4, physical pin 7 |
| GND / - | Ground, physical pin 6 |

If the sensor is a bare four-pin DHT22, add a 4.7k-10k pull-up resistor from DATA to 3.3 V. Many three-pin AM2302 breakout modules already include this pull-up.

The Raspberry Pi GPIO ribbon breakout/cobbler is only a pin adapter. It does not add the DHT22 DATA pull-up resistor. Use one available 10k resistor from DATA to 3.3 V for the AM2302/DHT22 bench setup. For a closer 4.7k-ish pull-up, put one 10k in parallel with five 1k resistors in series; that gives about 3.3k, which is still suitable for a short 3.3 V breadboard run. Do not use the 51/150/360 ohm resistors as a pull-up here, and do not pull DATA up to 5 V.

To gather AM2302/DHT22 ambient data in a CSV file:

```sh
offgrid-supervisor \
  --no-classic \
  --ambient-kind dht22 \
  --ambient-gpio 4 \
  --ambient-log-path /srv/offgrid/logs/ambient.csv
```

Use `--no-ambient` for development runs without the sensor attached. Use `--no-classic` for ambient-only bench runs before the charge controller network is available.

Configuration can be supplied with CLI flags or environment variables:

```sh
CLASSIC_HOST=192.168.0.10
CLASSIC_PORT=502
CLASSIC_DEVICE_ID=10
SUPERVISOR_REFRESH_SECONDS=5
SUPERVISOR_DISPLAY_CLEAR=true
BATTERY_CAN_PROTOCOL=pylon
AMBIENT_SENSOR_ENABLED=true
AMBIENT_SENSOR_KIND=ds18b20
AMBIENT_DHT22_GPIO=4
AMBIENT_DS18B20_DEVICE_ID=
AMBIENT_LOG_PATH=/srv/offgrid/logs/ambient.csv
WEATHER_ENABLED=true
WEATHER_LATITUDE=48.000000
WEATHER_LONGITUDE=-84.000000
WEATHER_LABEL=cabin
WEATHER_REFRESH_MINUTES=30
WEATHER_CACHE_PATH=/srv/offgrid/data/weather-cache.json
```

Design intent: keep device adapters, snapshot assembly, display rendering, and future control policy separate. That lets the terminal display be the first production view without making it the only interface.

Hardware adapters and conservative control logic for the Raspberry Pi.

Planned responsibilities:

- Read sensors and device interfaces.
- Publish normalized telemetry.
- Evaluate control policies.
- Apply safety checks before changing outputs.
- Expose health information for monitoring.
