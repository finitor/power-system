# Pi Controller

Read-only supervisory monitoring and future control orchestration for the Raspberry Pi.

## Current Scaffold

- `src/offgrid_power/classic.py`: MidNite Classic Modbus TCP telemetry adapter.
- `src/offgrid_power/supervisor.py`: combines adapter reads into a single snapshot.
- `src/offgrid_power/terminal_display.py`: renders a compact terminal status view.
- `src/offgrid_power/cli/supervisor_display.py`: production entry point for the live terminal display.
- `../../scripts/supervisor-display.py`: compatibility wrapper for local repo runs.

Run from the repo root:

```sh
source .venv/bin/activate
python -m pip install -e .
offgrid-supervisor --classic-host 192.168.0.10
```

Use `--once` for a single snapshot. The current scaffold is read-only and performs no control writes.

Configuration can be supplied with CLI flags or environment variables:

```sh
CLASSIC_HOST=192.168.0.10
CLASSIC_PORT=502
CLASSIC_DEVICE_ID=10
SUPERVISOR_REFRESH_SECONDS=5
SUPERVISOR_DISPLAY_CLEAR=true
```

Design intent: keep device adapters, snapshot assembly, display rendering, and future control policy separate. That lets the terminal display be the first production view without making it the only interface.

Hardware adapters and conservative control logic for the Raspberry Pi.

Planned responsibilities:

- Read sensors and device interfaces.
- Publish normalized telemetry.
- Evaluate control policies.
- Apply safety checks before changing outputs.
- Expose health information for monitoring.
