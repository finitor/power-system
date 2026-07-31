# Magpie Camp Power System

In June 2026 I upgraded my off-grid cabin's energy storage to LiFePO4,
triggering a cascade of long-desired improvements in capacity, instrumentation,
and control. Planning and implementing all that here with Codex & Claude.

I expect this will be interesting to outsiders mainly for the mechanics of
interfacing to all the machinery of a solar power system from a Raspberry Pi.
But possibly also for seeing how the AIs help a moderately experienced
electrical engineer structure a medium-complexity project that mixes
hardware, software, and legacy device support.

## Goals

- Track the physical system by major subsystem: battery bank, PV arrays, charge controllers, inverter/charger, supervisor, loads, sensors, wiring, fuses, and enclosures.
- Run reliable local telemetry on a Raspberry Pi.
- Keep control logic conservative, inspectable, and recoverable.
- Preserve field observations, photos, diagrams, and configuration in one versioned place.

## Current Status

In service. A Raspberry Pi supervisor polls the battery bank (CAN), two
solar charge controllers — the MidNite Classic on array 0 (Modbus TCP, read
and write) and the EPEver TEP10425 on array 1 (Modbus RTU, read and write) —
and the MagnaSine inverter/charger (Magnum network RS-485, read-only) on
per-device actor threads. A closed-loop charge allocator distributes the
BMS's tapering charge-current budget evenly across both controllers. The
supervisor serves a Kindle wall display, a desktop terminal console, and a
JSON API behind nginx, with metrics in local SQLite and store-and-forward
export to object storage.

In progress: battery temperature control (heater/ventilation chain) and
finishing the array 1 physical install (the EPEver is the committed array 1
controller — selection is settled). Closed-loop supervisory control of the
Magnum charger was considered and **rejected** (generator charging stays
human-attended; see
[decision 0002](docs/decisions/0002-magnum-remote-takeover.md)) — the Magnum
is a read-only telemetry tap. See
[docs/engineering-plan.md](docs/engineering-plan.md) and
[docs/journal/](docs/journal/) for the live state of work.

## Repo Map

- [docs/architecture.md](docs/architecture.md): system overview and data/control flow.
- [docs/subsystems/](docs/subsystems/): telemetry, control, wiring, and open questions for each major hardware subsystem.
- [hardware/inventory.csv](hardware/inventory.csv): single source of truth for components; [docs/hardware-inventory.md](docs/hardware-inventory.md) explains the format and views.
- [docs/wiring.md](docs/wiring.md): wiring notes, pinouts, fuse ratings, cable gauges, and labels.
- [docs/commissioning.md](docs/commissioning.md): first power-up and acceptance checklist.
- [docs/runbooks/](docs/runbooks/): action-oriented procedures for installation, changeovers, maintenance, and recovery — including [running-the-supervisor.md](docs/runbooks/running-the-supervisor.md) (how to launch it).
- [docs/maintenance.md](docs/maintenance.md): backup, restore, inspection, and recovery routines.
- [docs/troubleshooting.md](docs/troubleshooting.md): symptoms, likely causes, and checks.
- [docs/safety.md](docs/safety.md): electrical and operational safety notes.
- [docs/engineering-plan.md](docs/engineering-plan.md): living backlog of hardening and architecture work.
- [docs/decisions/](docs/decisions/): architecture decision records (append-only judgments with context and consequences).
- [docs/journal/](docs/journal/): append-only engineering journal — dated session narratives of what was tried, measured, and learned.
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
- Git is the source of truth; the Pi checkout is disposable. Never hand-edit on the Pi without reconciling back — work on the Mac, commit + push, then `deploy.sh`. See [CONTRIBUTING.md](CONTRIBUTING.md) and [Marooned changes](docs/runbooks/running-the-supervisor.md#marooned-changes-the-rule-that-keeps-biting-us).

## Quick Start

**Running the Pi supervisor** (launch, venv setup, env file, command-line
arguments, manual/bench runs): see
**[docs/runbooks/running-the-supervisor.md](docs/runbooks/running-the-supervisor.md)**.
In short, it runs as the `offgrid-supervisor` systemd service; deploy changes
with `bash scripts/deploy.sh` on the Pi.

This local directory has already been initialized as a Git repo and the scaffold has been committed.

```sh
git status
```

See [docs/github-sync.md](docs/github-sync.md) for publishing this local repo to GitHub.
