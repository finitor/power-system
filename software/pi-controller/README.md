# Pi Controller

Read-only supervisory monitoring and future control orchestration for the Raspberry Pi.

## Current Scaffold

- `src/offgrid_power/classic.py`: MidNite Classic Modbus TCP telemetry adapter.
- `src/offgrid_power/ambient.py`: AM2302/DHT22 ambient temperature and humidity adapter.
- `src/offgrid_power/canbus.py`: SocketCAN discovery and Pylon-style battery CAN decoding helpers.
- `src/offgrid_power/supervisor.py`: combines adapter reads into a single snapshot.
- `src/offgrid_power/terminal_display.py`: renders a compact terminal status view.
- `src/offgrid_power/cli/can_decode.py`: live or log-based battery CAN decoder.
- `src/offgrid_power/cli/supervisor_display.py`: production entry point for the live terminal display.
- `../../scripts/supervisor-display.py`: compatibility wrapper for local repo runs.

Run from the repo root:

```sh
source .venv/bin/activate
python -m pip install -e .
offgrid-supervisor --classic-host 192.168.0.10
```

Use `--once` for a single snapshot. The current scaffold is read-only and performs no control writes.

To inspect the Eco-Worthy/Pylon-style battery CAN bus from the Pi:

```sh
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 listen-only on
sudo ip link set can0 up
offgrid-can-decode --interface can0 --seconds 3 --raw
```

Keep `can0` in listen-only mode while validating telemetry. The CAN decoder currently treats writable/control behavior as unavailable and only decodes battery-to-inverter telemetry and permissive/request frames.

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
  --ambient-log-path /var/log/offgrid-power/ambient.csv
```

Use `--no-ambient` for development runs without the sensor attached. Use `--no-classic` for ambient-only bench runs before the charge controller network is available.

Configuration can be supplied with CLI flags or environment variables:

```sh
CLASSIC_HOST=192.168.0.10
CLASSIC_PORT=502
CLASSIC_DEVICE_ID=10
SUPERVISOR_REFRESH_SECONDS=5
SUPERVISOR_DISPLAY_CLEAR=true
AMBIENT_SENSOR_ENABLED=true
AMBIENT_SENSOR_KIND=ds18b20
AMBIENT_DHT22_GPIO=4
AMBIENT_DS18B20_DEVICE_ID=
AMBIENT_LOG_PATH=/var/log/offgrid-power/ambient.csv
```

Design intent: keep device adapters, snapshot assembly, display rendering, and future control policy separate. That lets the terminal display be the first production view without making it the only interface.

Hardware adapters and conservative control logic for the Raspberry Pi.

Planned responsibilities:

- Read sensors and device interfaces.
- Publish normalized telemetry.
- Evaluate control policies.
- Apply safety checks before changing outputs.
- Expose health information for monitoring.
