# EPEver TEP10425 Integration Notes

Unit on hand 2026-06-10. Manual: `~/Dropbox/manuals/solar/Epever-TEP-Manual-EN-V1.1.pdf`
(60 pp, v1.1). Notes here focus on what integration with the supervisor and
the Cubix bank needs; page refs are manual page numbers.

## Live register corrections (2026-06-16) — the generic Tracer map does NOT hold

The TEP's input/status register layout is **offset and word-swapped** versus
the generic EPEver/Tracer map. Confirmed live during the array-1 dry-run:

- **Charging-equipment status is `0x3202`, not `0x3201`.** The generic-map
  `0x3201` reads a flat `0x0000` even while charging; the real status word is at
  `0x3202` (`0x0009` = D0 running + D3–D2=2 → Boost while charging at 3.4 A). The
  bit decode `(raw >> 2) & 0x03` and the status dictionary were already correct —
  only the source register was wrong. `read()` now pulls `0x3200` ×3 and decodes
  `status[2]`. This is why the EPEver showed a phantom "Resting" indefinitely.
- **Energy block is big-endian by word** (opposite the live `0x3100` registers),
  so decode generation counters **high-word-first**:
  - `0x330C/0x330D` = **generated today** (RTC-day-anchored, resets at device midnight).
  - `0x3310/0x3311` = **lifetime generated total** (monotonic — survived the RTC
    date jump that reset today/month, and keeps climbing). This is the counter the
    windowed-consumption / autonomy calc differences.
  - `0x3313` (=200) is a constant field, **not** energy.
  - Scaling provisional (`/100` = kWh); the magnitude reads low vs energy
    delivered, so confirm against the panel's accumulated-kWh.
- **`0x311A` is not SOC** (read 1727%); the controller has no BMS link, so it
  can't know SOC. Code guards it to `None`.
- **RTC at holding `0x9019..0x901B`.** Encoding (confirmed by readback):
  `r0 = minute<<8 | second`, `r1 = day<<8 | hour`, `r2 = (year-2000)<<8 | month`.
  Found mis-set to **2024-10-06** (~20 months + ~12 h off) — the crystal keeps
  good time but the value was wrong (likely a dead RTC backup defaulting on power
  loss). Set to local time 2026-06-16; the date jump immediately reset the
  today/month buckets, confirming they're RTC-driven. **Open: does the set survive
  a power-cycle?** If not, daily counters can't be trusted — which is why the
  autonomy design differences the monotonic *total* and never relies on the clock.

## Battery profiles (3.3.5)

- Native **LFP16S** profile (48 V, 16S — our bank): OVD 58.4 V, charging
  limit 57.2 V, EQ/Bulk 56.8 V, Float 54.0 V, bulk recovery 52.0 V, LVD
  45.2 V. Stock values are hotter than our practice (Classic runs absorb
  54.4 / float 54.1).
- **User-define** range 36–62 V with an enforced ordering ruleset
  (OVD > CLV ≥ EQ ≥ Bulk ≥ Float > Bulk Recovery, etc.), so a conservative
  profile matching the Classic's setpoints is settable.
- Lithium battery protection auto-enables when a lithium type is selected;
  manual warns BMS accuracy must be ≤ 0.2 V.

## Native BMS closed-loop mode (3.3.6) — the headline

With **BPRO (BMS protocol)** set and **UBS (Use BMS Settings) = ON**, the
controller follows the BMS directly:

- charges per the BMS-published charging voltage upper limit and discharge
  lower limit (with a defined conversion table deriving all 12 control
  voltages from those two limits);
- limits charge current to the BMS-published charging limit current;
- honors BMS forced-charge requests and full-charge (BCF) status.

This is natively the closed-loop charge control our charger-current taper
approximates over Modbus for the Classic — **but on present evidence it is
not usable as the policy plane with our bank.** Across 1,487 recorded
supervisor snapshots the Cubix has published exactly one CVL (58.4 V =
3.65 V/cell, its protection maximum) and one CCL (200 A): a static
protection envelope, not a managed charge target. UBS following those
limits would charge far hotter than our 54.4 V practice. Good BMSes
(e.g. Pylontech) walk CVL/CCL down dynamically near full, which is what
makes UBS-style modes genuinely closed-loop; whether the Cubix does this
near 100% SOC has never been observed and is now a deciding bench
question. Until proven otherwise: **UBS stays off**, the EPEver runs a
conservative user-defined profile, and the supervisor's taper is the
policy authority for this controller too (the taper abstractions are
charger-agnostic by design).

## Ports and pinouts (1.2.1)

**COM port** (RJ45, isolated, Modbus RS485; supplies 5VDC/200mA on pin 1):

| Pin | Function |
|---|---|
| 1 | +5VDC — leave disconnected |
| 3 | RS485-B |
| 6 | RS485-A |
| 8 | GND |

Bench wiring note with the DSD TECH SH-U11H adapter: the working connection is
controller pin 3 (RS485-B) to adapter A/D+, controller pin 6 (RS485-A) to
adapter B/D-, and pin 8 to adapter GND. Pin 1 is +5V and must remain
disconnected.

Bench wiring note with the KL0823B 2-wire adapter: when using a
straight-through CAT-6 patch cable into a breakout, the working connection is
controller pin 6 to adapter A and controller pin 3 to adapter B. The labels are
therefore opposite the SH-U11H test wiring above; if telemetry is absent after
moving the cable from a Magnum tap, swap A/B before changing anything else.

**Port 9** (RTS/BMS/CAN multiplexed): correction to the accessories-section
reading — the BMS-Link module is only needed for *other* manufacturers'
BMS protocols. **Pylon-protocol batteries connect directly to port 9 with
BMS protocol number 21** (Pylon cable CC-RJ45-RJ45-PYLON-200), and the
RTS-D47K temp sensor uses protocol number 32.

Full port-9 pinout, confirmed from the manual §1.2.1 (it carries **both**
RS485 and CAN on the same RJ45, sharing GND on pin 8):

| Pin | Definition | Pin | Definition |
|---|---|---|---|
| 1 | / | 5 | CAN-L |
| 2 | / | 6 | RS485-A |
| 3 | RS485-B | 7 | / |
| 4 | CAN-H | 8 | GND |

The manual does **not** state which transport (RS485 3/6 vs CAN 4/5) a given
BMS protocol number rides — it punts to the EPEVER website's protocol table.
The 2026-06-12 session assumed Pylon-21 was RS485 and wired pins 3/6; it heard
nothing at any baud. Since the Cubix is proven to emit Pylon frames over CAN,
the open hypothesis is that Pylon-21 here is **CAN-based** (pins 4/5), which
would explain the silent RS485 tap. Decide it by sniffing CAN on pins 4/5
with BPRO=21 set.

**Port-9 CAN sniff (single-adapter bench procedure).** With only one
SH-C31G, move it off the Cubix for the test. Wiring is nearly identical to
the Cubix CAN port — CAN-H/CAN-L are the same pins; only GND moves:

| SH-C31G | Cubix CAN | EPever port 9 |
|---|---|---|
| CANH | pin 4 | pin 4 |
| CANL | pin 5 | pin 5 |
| GND | pin 3/6 | pin 8 (optional on isolated adapter, short run) |

- Leave the SH-C31G's onboard terminator enabled (sw1 up / sw2 down ≈ 125 Ω);
  whether port 9 terminates CAN internally is unconfirmed, so be the one
  guaranteed terminator.
- Stop the supervisor and `offgrid-can-watchdog.timer` first — the watchdog
  auto-resets `can0` every ~2 min and will fight the experiment.
- Bring `can0` up listen-only at 500 kbit/s (Cubix bitrate; Pylon-CAN is
  usually 500k) and `candump can0`. If silent, sweep bitrates via the
  `can_survey` CLI — listen-only, so harmless.
- A clean silent bus (zero frames AND zero error counters) cannot distinguish
  "EPever not transmitting" from an open wire — see
  [troubleshooting.md](../troubleshooting.md). The pin-4/5 match to the Cubix
  cable removes the wiring variable, which is the point of reusing it.

## Remote parameter setting (3.3.7)

- "USER" voltage parameters settable via PC software through the **COM
  port** (RJ45) with a USB-to-RS485 cable — our ordered Waveshare adapter's
  job. Also via optional WiFi module + cloud app (not interesting; we are
  local-first).
- The manual contains **no Modbus register map** — EPEver publishes that
  separately (B-series/Tracer-family register doc). Bench task: confirm
  the TEP10425 answers the standard EPEver Modbus RTU registers and which
  are writable.

## Solar Guardian RS485 sniff session (2026-06-11)

Bench setup:

- Solar Guardian ran on a Windows 8.1 PC through a DSD TECH SH-U11H
  USB-RS485 adapter, recognized as a Prolific PL2303GC serial port on
  `COM6`.
- A KL0823B 2-wire USB-RS485 adapter on the Raspberry Pi passively sniffed
  the same A/B pair at 115200 baud, device address 1, with CPE ON.
- Sniffer log path on the Pi during the session:
  `~/power-system/data/epever-solar-guardian-sniff.log`.
- No PV was connected, so charge-output and PV-current observations were
  harmless zero-output tests.
- Wiring observation: the SH-U11H ground connection was inadvertently
  removed during the session with no apparent effect on Solar Guardian
  communication. The TEP10425 COM port appears to tolerate two-wire A/B
  control in this bench setup, though pin 8 GND remains the documented
  reference connection when available.
- Follow-up burn-in decision: move supervisor EPEver control to the
  KL0823B 2-wire CH340 adapter for a multi-day trial, leaving Magnum
  telemetry/control disconnected and the Magnum operated from the OEM
  remote. The udev symlink `/dev/epever-rs485` temporarily points at the
  CH340 adapter for this trial.

Critical behavioral finding: **charge-control voltage writes appear to
require Battery Type = User.** With the local lithium/LiFePO4 preset active,
Solar Guardian either would not expose individual voltage writes or the
controller did not retain the attempted voltage change. After switching
Battery Type to `User`, Solar Guardian successfully wrote the battery
control voltage block and read back the changed equalization voltage. Treat
User profile as a precondition for any supervisor-owned voltage control.

Solar Guardian writes normal Modbus RTU function `0x10` (Write Multiple
Registers). No special unlock transaction was observed. Earlier single
register `0x06` writes were not accepted by this controller, so the working
writer should use `0x10`, including for one-register writes.

### Holding-register writes and settings

Battery-control voltage block, written as one block starting at `0x9007`
with count 12. Values are centivolts (`raw / 100 = V`). This is the
production-safe way to change charge voltages: read the full block,
modify the intended fields, validate ordering, and write the full block
back.

| Register | Solar Guardian label | Observed raw | Observed value |
|---:|---|---:|---:|
| `0x9007` | Overvoltage Disconnect Voltage | 6400 | 64.00 V |
| `0x9008` | Charging Limit Voltage | 6000 | 60.00 V |
| `0x9009` | Overvoltage Recovery Voltage | 6000 | 60.00 V |
| `0x900A` | Equalization Charging Voltage | 5830 | 58.30 V |
| `0x900B` | Bulk Charging Voltage | 5760 | 57.60 V |
| `0x900C` | Float Charging Voltage | 5520 | 55.20 V |
| `0x900D` | Bulk Voltage Recovery Voltage | 5280 | 52.80 V |
| `0x900E` | Low Voltage Recovery Voltage | 5040 | 50.40 V |
| `0x900F` | Undervoltage Alarm Recovery Voltage | 4880 | 48.80 V |
| `0x9010` | Undervoltage Alarm Voltage | 4800 | 48.00 V |
| `0x9011` | Low Voltage Disconnect Voltage | 4440 | 44.40 V |
| `0x9012` | Discharging Voltage Limit Voltage | 4240 | 42.40 V |

Captured block write after changing Equalization Charging Voltage to
58.3 V:

```text
01 10 90 07 00 0c 18
  19 00 17 70 17 70 16 c6 16 80 15 90
  14 a0 13 b0 13 10 12 c0 11 58 10 90
  38 76
```

Current and related control registers:

| Register | Solar Guardian label | Encoding | Observed behavior |
|---:|---|---|---|
| `0x9001` | Battery Capacity | Ah | Read back 198 Ah after user test edits. |
| `0x9004` | Screen Backlight Time | seconds | Read back 60. |
| `0x9005` | Screen Cycle Time | seconds | Read back 2. |
| `0x9013` | BAT Max Charging Current | centiamps (`raw / 100 = A`) | Write 99 A -> 100 A produced raw `0x2710` = 10000 = 100.00 A. This is the primary taper knob. |
| `0x9014` | Bulk Charging Time | minutes | Read back 10. |
| `0x9015` | Equalize Charging Time | minutes | Read back 10. |
| `0x9019..0x901B` | Device Time | encoded date/time | Read as a 3-register block; encoding not decoded yet. |
| `0x901E` | Device Temperature Upper Limit | centidegrees C | Read back 8500 = 85.00 C. |
| `0x901F` | Device Over Temperature Recovery | centidegrees C | Read back 7500 = 75.00 C. |
| `0x9038` | Battery Charging Mode | enum | Read `0`, displayed as Voltage. |
| `0x9039` | Full Charge Protection SOC | percent | Read back 99. |
| `0x903A` | Full Charge Protection Recovery SOC | percent | Read back 95. |
| `0x903C` | Low Battery Alarm Recovery SOC | percent | Read back 10. |
| `0x903D` | Low Battery Alarm SOC | percent | Read back 8. |
| `0x903F` | Data Record Period | minutes | Read back 10. |
| `0x9040` | BMS Protocol | enum | Read back 32. |
| `0x9041` | Use BMS Settings | enum | Read `1`, displayed as ON. |
| `0x9042` | PV Connection Mode | enum | Read `0`, displayed as Independent. |
| `0x9043` | Simulate BMS Mode | enum | Read `0`, displayed as Disable. |
| `0x9045` | Device ID | address | Read back 1. |
| `0x9046` | Device Baud Rate | baud / 100 | Read `1152`, displayed as 115200 Bd. |
| `0x9047` | Parallel Max Charging Current | amps | Write 1199 was not retained; write 1190 was accepted; restored to 1200. Treat as 10 A step. |
| `0x9049` | PV Restart Charging Period | minutes | Read back 10. |

Unknown or avoid-for-now settings observed on the Battery Parameter tab:
`0x9017`, `0x901C`, and `0x901D` appear tied to temperature or lithium/BMS
limits but were not decoded in this session. Do not write them until
mapped.

### Home/status polling

Solar Guardian's Home page continuously polls input registers with
function `0x04`. The page labels in the screenshot align with these
blocks:

| Register/block | Observed/label mapping |
|---:|---|
| `0x300F` | PV Amount; observed `2`. |
| `0x3100..0x3103` | PV electrical block; zero with no PV connected. |
| `0x3108..0x310B` | Additional PV/current/power block; zero with no PV connected. |
| `0x3114` | Battery Voltage; observed around `0x14d6..0x14d9` = 53.34-53.37 V. |
| `0x3117..0x311A` | Battery/status detail block; includes device temperature at `0x311A` (`0x07DA` = 20.10 C observed). |
| `0x311E..0x3121` | Additional live/status block; zero during no-PV test. |
| `0x3200` | Device/status word; Home showed device Normal. |
| `0x3202..0x3203` | Charging/fault status; Home showed Not Charging. |
| `0x3205` | Additional status/fault flag; Home showed Device OverHeat: No. |
| `0x3301..0x3302` | Energy/statistic block; observed nonzero value on Home. |
| `0x330B..0x3312` | Generation counters; repeated raw `0x0130` = 304 = 3.04 kWh for day/month/year/total. |
| `0x3402` | Consumption/other energy counter; observed zero. |

The sniffer grouped each Home polling burst into one long line, so those
log lines report `crc=bad` for the aggregate buffer. The individual
Modbus request/response frames inside the burst are still usable and match
the Solar Guardian UI values.

### Control conclusions from sniffing

- Production voltage control should use User battery type, read-modify-write
  of `0x9007..0x9012`, and local validation of the controller's voltage
  ordering rules before any write.
- Production current taper should use `0x9013` BAT Max Charging Current,
  encoded in centiamps. The manual range for TEP10425 is 1-100 A, step
  1 A.
- `0x9047` Parallel Max Charging Current is real and writable, but it is a
  parallel-system ceiling rather than the normal taper knob. It appears to
  require 10 A increments.
- ~~No true charger enable/disable register was discovered.~~ **Superseded
  2026-06-15:** there *is* a true charge enable/disable — it is a **coil**,
  not a holding register, which is why holding-register sniffing missed it.
  See "Modbus control surface" below. Charge "disable" no longer needs
  conservative setpoints or an external disconnect.
- A later PV-connected test should repeat the Home-page sniff while the
  controller is actually charging, to map the charging-current/power fields
  and status-bit transitions under load.

## Modbus control surface (COM port = port 8)

Established 2026-06-15. The COM port (manual item 8, RS485 Modbus, pins
3=B/6=A/8=GND) is where the supervisor controls the controller. Two distinct
spaces matter:

### Coils (function 0x01 read / 0x05 write) — the missing on/off switches

These are single-bit read/write outputs, a separate address space from the
holding registers (coil 0 ≠ holding register 0). All prior control work used
holding registers (function 0x10), so the coils were never read — which is
why the charge on/off control was invisible.

| Coil | Name | Charge-control use |
|---:|---|---|
| **0x0000** | **Charging device on/off** (1=on, 0=off) | **TRUE 0 A charge stop.** Verified read/write/reversible 2026-06-15, and **current-zeroing confirmed under live PV 2026-06-16** (drove 4 A → 0.00 A and back). The hard-stop the current taper can't give (`0x9013` floors at 1 A). Now in code: `EpeverClient.set_charging()`. |
| 0x0001 | Output control mode manual/auto | load output (not charge) |
| 0x0002 | Manual control the load | load output |
| 0x0003 | Default control the load | load output |
| 0x0005 | Enable load test mode | load test |
| 0x0006 | Force the load on/off | load test |
| 0x000D | Restore system defaults | **⛔ destructive — would wipe User profile / BPRO / setpoints; never write** |
| 0x000E | Clear generation statistics | **NO-OP on the TEP** — verified 2026-06-16 (byte-identical 0x3300 energy block before/after a pulse). The panel's CAE maps elsewhere or is panel-only. Don't rely on it; the windowed-energy design needs no clear anyway. |

The controller's load-output terminal is not in our power path (all loads are
on the 48 V bus / inverter per `../wiring.md`), so the load coils are doubly
irrelevant. Coil `0x0000` is the only charge-control coil.

Supervisor primitive: `EpeverClient.set_charging(False)` to stop charging,
`set_charging(True)` to resume (coil `0x0000`, with read-back verify); also
`scripts/epever-coil.py charge {on|off}`. Current-zeroing **confirmed under
PV 2026-06-16** (4 A → 0.00 A → back). Still unconfirmed: whether the off
state survives a power-cycle / PV-restart (if not, re-assert each poll).

### Charge-knob holding registers (TEP-specific 0x9000 map; live values 2026-06-15)

The `0x9000` settings block is TEP-specific — derived from the Solar Guardian
sniff plus live reads, not any generic EPEver map. The `0x3000` input
registers and the coils above are standard, but do **not** trust a generic
`0x9000` layout here.

| Reg | Name | Live | Decoded | Role |
|---|---|---:|---|---|
| `0x9008` | Charging-limit voltage | 6000 | 60.00 V | hard CV ceiling; writer aborts targets above this |
| `0x900A` | Equalization V | 5440 | 54.40 V | EQ target |
| `0x900B` | Boost/absorption V | 5440 | 54.40 V | absorption target (primary policy knob) |
| `0x900C` | Float V | 5410 | 54.10 V | float target |
| `0x900D` | Boost-recovery V | 5280 | 52.80 V | re-enter-boost threshold |
| `0x9013` | Max charging current | 8000 | 80.00 A | current taper (1–100 A, centiamps; floors at 1 A) |
| `0x9014` | Boost/"Bulk" charging time | 120 | 120 min | absorption-hold duration (see caveat) |
| `0x9015` | Equalize charging time | 120 | 120 min | equalize duration |
| `0x9038` | Charge mode | 0 | Voltage | keep |
| `0x9039` / `0x903A` | Full-charge protect / recover SOC | 99 / 95 | % | SOC cutoff — **deprioritized**, see below |
| `0x9047` | Parallel max current | 1200 | 10 A steps | only if paralleling (N/A) |
| `0x9049` | PV restart period | 10 | 10 min | restart delay after cutoff/clouds |

(Boost/float read 54.40/54.10 = aligned to Classic practice, not stock
57.6/55.2 — actively managed by the Classic-copy sync.)

**Stage-duration caveat (`0x9014`).** Solar Guardian labels it "Bulk Charging
Time," but on a lithium config this is effectively the absorption/CV-hold
timer (hold `0x900B` boost voltage for this many minutes, then drop to float
`0x900C`). EPEver's bulk/boost labeling is loose and the TEP has no published
register doc, so confirm behaviorally under PV. For LiFePO4 a 120-min hold
every cycle is more than needed; trimming `0x9014` (or setting
`0x900B`≈`0x900C` to collapse absorption into float) reduces time-at-high-V
stress through the controller's own autonomous cycle.

**Why `0x9039` SOC cutoff is deprioritized.** It would need a trustworthy SOC
on the controller, which means a BMS link to the EPEver we don't have. More
fundamentally, the supervisor already owns *both* ends of the loop: it reads
SOC from the Cubix CAN broadcast and can drive coil `0x0000` / `0x9013` /
the voltage block directly over port 8. So SOC-based charge cutoff belongs in
the supervisor (`if soc >= X: write_coil(0, False)`), not delegated to the
controller's fixed internal behavior. `0x9039` is strictly dominated.

### Why the CAN / BMS-over-CAN path was abandoned (2026-06-15)

Coil `0x0000` makes the native closed-loop pursuit moot, but for the record:
all 35 EPEver BMS protocols are RS485 (Modbus/Telecom/User-Define) — **none
are CAN**; Pylon is protocol 21 = RS485, on port-9 pins 3/6. The port-9 CAN
broadcast (`0x17343732`, PGN 0x33400, `EDP=1` → proprietary, not RV-C/J1939)
is the controller's own parallel/monitoring telemetry, status-only. Active
CAN injection produced no response and no usable register write (a one-time
`0x9041` blip did not reproduce). There is no usable CAN-write path; the
true charge-stop is coil `0x0000` over port 8 instead.

## Operational gotchas spotted

- **CPE (Com Port Enable)**, default ON: if set OFF, external comms shut
  down when there is no PV input/charging — i.e. polling would die every
  night. Keep ON.
- PRCP (PV restart charging period) default 10 min — explains delayed
  restart after clouds; tunable 0–60 min.
- PMCC: parallel-controller max charging current exists (100–1,200 A
  range) — relevant if Classic + EPEver coordination is ever wanted,
  though they're on separate arrays.
- ARM and DSP firmware versions readable from the display — record them
  at commissioning.

## Aligning charge voltages to the Classic

With the Classic as the baseline, `scripts/epever-copy-from-classic.py`
copies the charge voltages (and, unless `--no-current`, the charge-current
limit) from the Classic to the EPEver:

| Classic source | EPEver target | Register |
|---|---|---|
| absorb voltage | boost (absorption) voltage | 0x900B |
| float voltage | float voltage | 0x900C |
| equalize voltage | equalize voltage | 0x900A |
| battery current limit | max charging current | 0x9013 |

It is dry-run by default (prints the diff, writes nothing); `--write`
applies. The voltage write is a read-modify-write of the 0x9007-0x9012 block
(function 0x10) that overwrites only the three charge-voltage cells and
preserves the protection thresholds (OVD/reconnect/LVD/discharge), which
have no Classic counterpart. It aborts if a target exceeds the EPEver's own
charging-limit ceiling (0x9008), and `EpeverClient.write_charge_voltages`
refuses unless Battery Type = User (code 6). Stop the supervisor before
running with `--write` so it isn't contending for the adapter.

## PV inputs and charge capacity (spec table, 2026-06-15)

Resolves the dual-PV-input questions (checklist 5/7).

- **Two PV inputs** (PV1/PV2), supported only on TEP10415/TEP10425.
- **Max PV input current: 50 A × 2** (50 A *per input*).
- **Rated charging current (battery-side output): 100 A** — the binding
  limit. The "rated charging power 5200 W" is just 100 A × ~52 V charge
  voltage; at 48 V it is ~4800 W.
- **Max PV open-circuit voltage: 250 V** at lowest temp / 225 V at 25 °C.
- **PV Connection Mode** = holding `0x9042`: **INDE (independent)** vs **CENT
  (centralized/paralleled)**. INDE tracks the two inputs as independent MPPT
  channels (two arrays, different orientation/strings allowed); CENT treats
  externally-paralleled inputs as one. **Our unit reads `0x9042 = 0 =
  Independent.**
- Tolerates over-paneling: charging-current/power limits + high-temp derate
  clip excess PV to the rated output safely.

**Capacity reasoning.** 5200 W/100 A is the controller's *total output
ceiling*, shared across both inputs — not a per-array figure. One 4s3p array
(~3200–3700 W STC, ~2800–3300 W realistic peak) uses roughly two-thirds of
it. A second array fills the headroom (~2000 W if co-oriented to avoid
simultaneous-peak clipping). But since the inputs are independent MPPT, a
*larger* second array on a different orientation (E/W or different tilt) is
usually the better play: peaks don't coincide, midday clipping is brief, and
total daily kWh is higher. Per-input 50 A and 250 V Voc easily accommodate a
2000–3500 W array (4s ≈ 140–180 V, well under 250 V; <25 A, well under 50 A).

## Bench checklist before installation

1. Power from a bench supply / battery, connect Waveshare RS485 to the COM
   port, confirm Modbus RTU comms (ID, baud) and read the register set.
2. Identify writable registers (battery type, user voltage params, charging
   limit current) and verify a write+readback of a harmless parameter.
3. Test BPRO/UBS against a Cubix pack on the BMS/CAN port: does it accept
   the Pylon CAN protocol? Verify it tracks BMS CVL/CCL.
4. **Deciding observation:** watch CAN frame 0x351 (CVL/CCL) through a
   genuine full-charge event — if the Cubix never modulates its limits,
   UBS is permanently unsuitable as the policy plane here.
5. ~~Spec-table questions for the dual PV inputs.~~ **Resolved** — see "PV
   inputs and charge capacity" above (50 A×2 inputs, 100 A output, INDE/CENT
   modes, ours Independent).
6. Record ARM/DSP firmware versions in the inventory notes.
7. PV-input Voc limit confirmed: **250 V cold / 225 V at 25 °C** — verify the
   4s3p string's cold Voc stays under 250 V before wiring.
