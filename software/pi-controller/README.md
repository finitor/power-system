# Pi Controller

Read-only supervisory monitoring and future control orchestration for the Raspberry Pi.

## Current Scaffold

- `src/offgrid_power/classic.py`: MidNite Classic Modbus TCP telemetry adapter.
- `src/offgrid_power/supervisor.py`: combines adapter reads into a single snapshot.
- `src/offgrid_power/terminal_display.py`: renders a compact terminal status view.
- `../../scripts/supervisor-display.py`: continuously updates the terminal display.

Run from the repo root:

```sh
source .venv/bin/activate
python scripts/supervisor-display.py --classic-host 192.168.0.10
```

Use `--once` for a single snapshot. The current scaffold is read-only and performs no control writes.

Hardware adapters and conservative control logic for the Raspberry Pi.

Planned responsibilities:

- Read sensors and device interfaces.
- Publish normalized telemetry.
- Evaluate control policies.
- Apply safety checks before changing outputs.
- Expose health information for monitoring.
