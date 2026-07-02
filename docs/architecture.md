# Architecture

## System Overview

This project is organized around the major power-system subsystems that require telemetry, documentation, and possibly supervisory control:

- [Site](site.md): Wawa, Ontario installation context, time zone, and solar-noon calculation notes.
- [Power budget](power-budget.md): measured load, calibrated PV effectiveness, the annual power-balance model, winter operating modes, and expansion analysis.
- [Battery bank](subsystems/battery-bank.md): 2x Eco-Worthy Cubix 100 48 V 100 Ah rack-mount batteries.
- [Solar charge controllers](subsystems/charge-controller.md): MidNite Solar Classic 200 (array 0) and EPEver TEP10425 (array 1).
- [Battery temperature control](subsystems/battery-temperature-control.md): winter warming, warm-weather ventilation, and charge-temperature permissives.
- [Inverter/charger](subsystems/inverter-charger.md): MagnaSine 4448 inverter/charger.
- [Supervisory controller](subsystems/supervisory-controller.md): Raspberry Pi and local services.
- [Display services](subsystems/display-services.md): Kindle wall display, desktop console, and the nginx/systemd restart chain.

```mermaid
flowchart LR
  Solar0[PV Array 0] --> Classic[MidNite Classic 200]
  Solar1[PV Array 1] --> Epever[EPEver TEP10425]
  Classic --> Battery[2x Eco-Worthy Cubix 100 Battery Bank]
  Epever --> Battery
  Battery --> Inverter[MagnaSine 4448 Inverter/Charger]
  Battery --> Heater[Battery Enclosure Heater]
  Inverter --> ACLoads[AC Loads]
  Battery --> DCLoads[DC Loads]
  Generator[Generator / AC Input] --> Inverter
  Battery -. BMS / battery telemetry .-> Pi[Raspberry Pi]
  Classic -. controller telemetry .-> Pi
  Epever -. controller telemetry .-> Pi
  Inverter -. inverter telemetry .-> Pi
  Pi -. current-limit writes (charge allocator) .-> Classic
  Pi -. current-limit + coil writes (charge allocator) .-> Epever
  Pi --> Telemetry[Local Telemetry Storage]
  Pi --> Dashboard[Local Dashboard]
  Pi --> Alerts[Alerts]
  Pi --> Control[Supervisory Control Requests]
  Pi -. heat enable .-> Heater
```

## Major Subsystems

| Subsystem | Purpose | Notes |
|---|---|---|
| Battery bank | Stores DC energy and exposes battery/BMS state | 2x Eco-Worthy Cubix 100, 48 V, 100 Ah each |
| Charge controllers | Convert solar array output into controlled battery charging | MidNite Solar Classic 200 (array 0) and EPEver TEP10425 (array 1); the supervisor allocates per-controller current limits |
| Inverter/charger | Converts 48 V DC to AC and charges from AC input when available | MagnaSine 4448 |
| Battery temperature control | Keeps batteries inside a safe temperature window | 48 V ceramic heater, insulated/ventilatable enclosure, thermostat, Pi permissive, thermal cutoffs |
| Supervisory controller | Reads telemetry, logs state, displays dashboard, and coordinates non-critical control | Raspberry Pi |
| Data services | Telemetry transport, database, dashboard, logs | Local-first SQLite with Backblaze B2 store-and-forward; MQTT remains an optional live transport |

## Data Flow

1. Subsystem adapters read battery, charge controller, inverter/charger, and Pi health telemetry.
2. Measurements are normalized into named telemetry points.
3. Telemetry is stored locally, displayed, and optionally published to local transports.
4. During WAN windows, unsent metric samples are exported to object storage for later downstream import.
5. Alert rules evaluate measurements and notify operators.
6. Control policies evaluate state and request actuator changes only where explicitly documented.
7. Safety checks approve, reject, or force outputs to conservative states.

## Control Boundaries

Document which outputs the Pi is allowed to control and which safety functions are handled by dedicated hardware.

| Output | Controlled By | Default State | Manual Override | Notes |
|---|---|---|---|---|
| Battery protection | Battery BMS / hardware protection | Protected | Battery service procedures | Pi may monitor but should not be the primary safety device |
| Solar charging | MidNite Classic 200 (array 0) + EPEver TEP10425 (array 1) | Controller-managed regulation; Pi sets per-controller current limits (closed-loop charge allocator, live behind `--charge-allocation`) | Charge controller front panels / breakers; `systemctl stop offgrid-supervisor` or the unit flag disables Pi writes | Pi writes current-limit ceilings and the EPEver charge coil to hold net battery charge within the BMS CCL; the controllers still own voltage/CV regulation |
| AC inversion/charging | MagnaSine 4448 | Inverter-managed | Inverter controls / breakers | Pi may monitor and possibly request mode changes if supported |
| Battery heating | Thermostat / Pi permissive / hardware thermal cutoff | Off | Physical disconnect / fuse | Use low-power staged heat, not uncontrolled PV dump |
| Auxiliary loads | TBD relay/contactors | Off unless specified | Physical switch or breaker | Add only after wiring and fail-safe behavior are documented |

## LiFePO4 Charge Policy

The legacy charging equipment predates common LiFePO4 charge profiles. It may not understand that these batteries should not be held indefinitely at lead-acid-style float voltages. The supervisor therefore needs a system-level charge policy that watches every active charger, not just the solar charge controller.

Core policy intent:

- Allow bulk charging when the battery is below its normal full threshold and temperature permits charging.
- Allow absorb only long enough to complete a conservative LiFePO4 charge target.
- Avoid sustained float as the normal resting state.
- Disable equalization in normal operation.
- Treat any equalize-capable command path as manual-only unless a documented, non-LiFePO4 maintenance reason exists.
- Prefer charger rest, off, or reduced-voltage maintenance behavior after the bank is full.
- Re-enable charging only after SOC or voltage falls below a documented restart threshold.
- Alert if any charger enters equalize, or remains in float, absorb, or high-voltage output longer than the configured limit.

Candidate controlled chargers:

- Midnite Solar Classic 200 solar charge controller.
- Magnum 4448 inverter/charger when AC input or generator charging is available.

The exact implementation may differ per charger. The Classic may be managed through Modbus TCP if reliable write control is confirmed. The Magnum path may be managed through the ME-RC50/Magnum network only after passive telemetry and safe command behavior are verified. Until then, conservative manual settings and alerts are preferred over blind automated control.

## RS485 Device Adapter Architecture

For a cross-appliance view of physical/link/application layers, see
[Network Stack Map](network-stack.md).

The Pi runs two separate USB RS485 adapters, each keyed to a stable `/dev` symlink by udev so they never swap on re-enumeration:

- **KL0823B / generic 2-wire CH340 on direct Pi USB path `1.2`** → `/dev/magnum-rs485`, the Magnum inverter network (`MagnumClient`, read-only telemetry tap).
- **KL0823B CH340 on USB path `1.5.3`** → `/dev/epever-rs485`, the EPEver TEP10425 COM port (Modbus RTU, read and write).

CH340 adapters expose no unique USB serial, so udev keys these names on the Pi's physical USB topology. If the two CH340 dongles are swapped between physical USB ports, their logical roles swap with the ports.

Each adapter is a distinct physical bus, so today the transport and protocol still live together per device class (`MagnumClient`, `EpeverClient`) rather than sharing one transport. The transport/device split below is the refactor point only if two devices ever need to share a single adapter; don't introduce it earlier.

- **Transport layer** — owns the serial port: device path, baud rate, open/close, raw read/write. One instance per physical adapter. Swappable independently of device logic.
- **Device adapter** — owns the protocol: one class per device type. Takes a transport, not a device path. Produces a normalized snapshot in the same style as `ClassicTelemetry` and `PylonCanSnapshot`.

## BMS Disconnect And Charger Transients

The BMS must be treated as last-ditch battery protection, not as the normal charge-control mechanism. If a lithium BMS opens while a legacy charger or MPPT controller is actively delivering current, the battery bus can experience a load-dump transient: the charger suddenly loses the battery as its voltage reference and energy sink.

Mitigation strategy:

- Keep charger voltage, current, absorb time, float behavior, equalization, and temperature limits conservative enough that the BMS should not need to open during normal operation.
- Use BMS telemetry, battery temperature, cell/battery voltage, and charger state to request charge reduction before a BMS cutoff threshold is reached.
- Prefer command-based charger shutdown first: Classic Mode Off or current-limit reduction for solar, Magnum charger standby/off for AC charging.
- If hardware interruption is required, interrupt the charger input/source side before opening the battery side: PV input before Classic battery output, AC/generator input before Magnum DC battery connection.
- Avoid opening a battery contactor or relying on the internal battery BMS while chargers are still actively pushing current.
- Alert on any BMS charge-disconnect, overvoltage, low-temperature charge inhibit, or unexpected battery disappearance during active charging.
- Consider DC-rated surge suppression as a secondary protective layer only; it does not replace proper charger inhibit sequencing.

Desired sequence for a charge-disallow event:

1. Supervisor sees approaching limit or BMS charge-not-allowed state.
2. Supervisor commands active chargers to stop or reduce current to zero.
3. Supervisor verifies charge current has fallen below a small threshold.
4. Only then may any downstream disconnect or contactor open.
5. Recovery requires charger state, battery voltage, temperature, and BMS status to be back inside the documented restart window.
