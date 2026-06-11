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
| MidNite Classic 200 | Modbus TCP | 192.168.0.10:502, device id 10 | Supervisor reads telemetry and reads/writes charge settings (charger current taper) |
| Eco-Worthy Cubix bank | CAN 500 kbit/s | `can0` via SH-C31G, listen-only | Pylon-protocol frames; BMS may go silent at idle and recover under load |
| MagnaSine MS4448PAE | OEM remote | ME-RC50 | Monitor and control from the OEM remote until further notice; supervisor Magnum telemetry is disabled |
| Victron BlueSolar MPPT 150/85 | VE.Can RJ45 | TBD on arrival | Telemetry path to be planned if selected |
| EPEver TEP10425 | RS485 Modbus RJ45 | `/dev/epever-rs485` (temporarily udev symlink to KL0823B CH340 for 2-wire control burn-in) | COM RJ45 pin 3 -> adapter A/D+, pin 6 -> adapter B/D-; pin 8 GND is documented but bench comms also worked with A/B only |
