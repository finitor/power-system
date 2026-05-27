# Off-Grid Power System

Raspberry Pi-mediated monitoring, logging, and control for an off-grid power installation.

## Goals

- Track the physical system by major subsystem: battery bank, solar charge controller, inverter/charger, controller, loads, sensors, wiring, fuses, and enclosures.
- Run reliable local telemetry on a Raspberry Pi.
- Keep control logic conservative, inspectable, and recoverable.
- Preserve field observations, photos, diagrams, and configuration in one versioned place.

## Current Status

Status: planning / initial scaffold

Update this section as the system moves through bench testing, installation, commissioning, and normal operation.

## Repo Map

- [docs/architecture.md](docs/architecture.md): system overview and data/control flow.
- [docs/subsystems/](docs/subsystems/): telemetry, control, wiring, and open questions for each major hardware subsystem.
- [docs/hardware-inventory.md](docs/hardware-inventory.md): component table.
- [docs/wiring.md](docs/wiring.md): wiring notes, pinouts, fuse ratings, cable gauges, and labels.
- [docs/commissioning.md](docs/commissioning.md): first power-up and acceptance checklist.
- [docs/maintenance.md](docs/maintenance.md): backup, restore, inspection, and recovery routines.
- [docs/troubleshooting.md](docs/troubleshooting.md): symptoms, likely causes, and checks.
- [docs/safety.md](docs/safety.md): electrical and operational safety notes.
- [hardware/bom.csv](hardware/bom.csv): bill of materials.
- [software/](software/): Pi services, telemetry, dashboard, and control code.
- [config/](config/): deployable service and application configuration templates.
- [scripts/](scripts/): installation, backup, restore, and health-check helpers.
- [photos/](photos/): installation photos.

## Operating Principles

- Monitoring code may fail quietly; control code must fail conservatively.
- Every controlled output should have a documented manual override or recovery path.
- Every physical wiring change should be reflected in documentation and photos.
- Secrets do not go in Git. Use `.env` files locally and keep `.env.example` in the repo.
- Subsystem docs are the source of truth for what should be measured, what may be controlled, and what still needs research.

## Quick Start

This local directory has already been initialized as a Git repo and the scaffold has been committed.

```sh
git status
```

See [docs/github-sync.md](docs/github-sync.md) for publishing this local repo to GitHub.
