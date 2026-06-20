# Hardware Inventory

The single source of truth for hardware is
[`hardware/inventory.csv`](../hardware/inventory.csv) — one row per physical
item (or line of identical units), with a stable `id` that other docs
reference.

Status vocabulary, in lifecycle order:

| Status | Meaning |
|---|---|
| `needed` | required or likely required, not yet purchased |
| `ordered` | purchased, not yet on hand |
| `on-hand` | physically at the site or bench |
| `installed` | in service in the system |
| `deferred` | optional; revisit if a stated condition arises |
| `retired` | no longer part of the plan, or removed from service |

Procurement view (filters the CSV; nothing to keep in sync):

```sh
python3 scripts/shopping-list.py        # needed + ordered
python3 scripts/shopping-list.py --all  # everything, grouped by status
```

Keep the CSV `notes` column to one line of current-state fact. Anything
longer — selection rationale, protocol findings, bench measurements —
belongs in the relevant subsystem doc or `docs/research/` note:

- Batteries and BMS telemetry: [battery bank](subsystems/battery-bank.md)
- Charge controllers, incl. second-controller selection:
  [charge controller](subsystems/charge-controller.md)
- Inverter/charger and Magnum network:
  [inverter/charger](subsystems/inverter-charger.md),
  [research](research/magnum-inverter-interface.md)
- Pi, power budgets, and comms adapters:
  [supervisory controller](subsystems/supervisory-controller.md)
- Heater and ventilation chain:
  [battery temperature control](subsystems/battery-temperature-control.md)

## Device Interfaces

Live communication paths as configured today:

| Device | Bus | Address / Port | Notes |
|---|---|---|---|
| MidNite Classic 200 | Modbus TCP | 192.168.0.10:502, device id 10 | Array 0. Supervisor reads telemetry and reads/writes charge settings; the charge allocator writes the current limit |
| Eco-Worthy Cubix bank | CAN 500 kbit/s | `can0` via SH-C31G, listen-only | Pylon-protocol frames; BMS may go silent at idle and recover under load |
| MagnaSine MS4448PAE | Magnum RS485 (read) + OEM remote (control) | `/dev/magnum-rs485` (SH-U11H); ME-RC50 | Supervisor reads a passive Magnum-network telemetry tap (live since 2026-06-16). Control stays manual on the ME-RC50 — closed-loop takeover rejected (decision 0002) |
| EPEver TEP10425 | RS485 Modbus RJ45 | `/dev/epever-rs485` (KL0823B CH340) | Array 1, committed and in service. KL0823B via straight-through CAT-6 breakout: COM RJ45 pin 6 -> adapter A, pin 3 -> adapter B; pin 8 GND is documented but bench comms also worked with A/B only |
