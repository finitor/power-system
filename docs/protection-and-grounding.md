# DC Protection and Grounding

System-wide overcurrent protection, grounding, and bonding for the off-grid plant
(battery, both charge controllers, inverter, AC distribution). This is a
cross-cutting concern, not battery-specific — the battery subsystem doc links
here rather than duplicating it.

Grounding and overcurrent protection are **invisible in normal operation and only
act during a fault**. "The system behaves as expected" is never evidence that
either is correct.

> Gauges and rules below are derived from the device manuals' NEC tables. This is
> a Canadian install — **confirm against the current CEC** before committing
> copper. The principles are stable; a gauge step may differ.

References: MagnaSine **MS4448PAE** manual §2.2 (`manuals/solar/Magnum-Inverter-Charger-MS4448PAE.pdf`);
MidNite **Classic** manual + MODBUS register map (`manuals/solar/ClassicUserManual.pdf`,
midnitesolar.com register map); original principle in [journal 2026-06-11-a](journal/2026-06-11-a.md).

## 1. Battery-leg breakers must be bidirectional

A **battery leg** (any conductor between the battery and a charge source/load)
carries current **both ways**: normal current one direction, **fault current the
other**. A charge controller pushes charge current *toward* the battery; a short
on the controller side makes the battery dump fault current *away* from it —
opposite directions through the same breaker.

A **polarized / unidirectional** DC breaker has a magnetic arc-blowout tuned for
one current direction. Wired into a bidirectional leg, it can **fail to
extinguish the arc** on a reverse-direction fault — the dangerous failure mode.

- **Battery legs → bidirectional protection required.**
- **PV-side (array → controller) may be polarized** — array current is
  essentially unidirectional, so a polarized breaker installed in the correct
  orientation is acceptable there.

Acceptable bidirectional devices: **hydraulic-magnetic** breakers (Carling/ETA),
MidNite **MNEDC**, or a **Class-T fuse** (direction-agnostic) plus a non-polarized
disconnect. Polarized DC MCBs (e.g. TOMZN TOB1Z) are **not** acceptable
straight-through on a battery leg.

### The series-opposed trick (polarized 2-pole → bidirectional)

A **2-pole polarized** breaker can be made bidirectional by wiring **both poles in
series, oriented opposite to each other**, on the one conductor. Whichever way
fault current flows, one pole's arc-blowout is correctly oriented and quenches it;
because the poles are mechanically ganged they trip together. This consumes both
poles to protect a single conductor. It is the documented way to deploy a
unidirectional 2-pole breaker on a battery leg — never straight-through.

## 2. How many conductors to protect

A battery fault is a series loop (`battery+ → fault → battery−`); **one** OCPD
anywhere in the loop interrupts it. Place that one OCPD on the **ungrounded**
conductor. Because this system's DC negative is the grounded (reference)
conductor (see §3):

- **Protect the POSITIVE conductor only — single-pole (or a series-opposed 2-pole
  on the positive).**
- **Never put a breaker in the NEGATIVE leg.** Two reasons: the standard rule
  against breaking a grounded conductor, **and** the negative is the Classic
  GFP's reference path (§3) — it must stay continuously connected for the GFP to
  protect the whole DC system.

(If the DC system were ever run truly floating/ungrounded, you would protect both
conductors. That is not this system.)

## 3. DC grounding — functionally grounded via the Classic GFP

The MidNite Classic's internal **Ground Fault Protection (GFP)** is the DC
system's single ground reference *and* its ground-fault detector. It ties DC
negative to ground through an internal **~1 Ω PTC**; on a ground fault (>~0.75 A
through the PTC) the PTC opens, the Classic detects it and **shuts down** (manual
reset required; the PTC self-heals). This satisfies the NEC DC-GFP requirement
with no external GFDI device.

**Consequences (important):**

- **Do NOT install a hard DC system-bonding jumper (SBJ).** Bonding the DC
  negative to ground anywhere else **defeats the GFP** (the manual is explicit).
  The GFP *is* the bond — a soft, protected one. *(This supersedes an earlier
  draft of this scheme that called for a hard #2/0–4/0 DC SBJ; that was wrong for
  a GFP-equipped system.)*
- **Only one GFP enabled** in the system (we have one Classic).
- The **Classic's equipment-ground terminal must be landed on the ground bus /
  earth** for the GFP to function. (Confirmed connected, 2026-06.)
- Keep all DC-negative connections **separate** from equipment-ground conductors
  except at this single reference.

**Confirmed state (2026-06, read from Classic reg 4187 `EnableFlagsBits`):**

| Protection | Bit | State |
| --- | --- | --- |
| GroundFaultEn (GFP) | 0x0001 | **ON** |
| ArcFaultEn | 0x0002 | **ON** (enabled 2026-06 via Modbus write to reg 4187, EEPROM-persisted; pending a Classic power-cycle to fully arm) |
| OCP / DefCon4 | 0x0040 | ON (leave as 1) |

The supervisor surfaces the GFP/arc-fault armed state (a "Protection" row on the
API and terminal views — kept off the Kindle) and raises a Classic arc- or
ground-fault **trip** as a Warnings-and-Faults **ERROR** (→ `/api/v1/health` 503).
Both arc and ground faults latch the Classic off until a manual breaker-cycle
reset, so the alerting is what makes them tenable on an unattended site.

## 4. AC grounding / bonding (MS4448)

The MS4448PAE is **chassis-isolated** and has **no internal neutral-ground bond**
(manual §2.2.2) — grounding is the installer's responsibility.

- **One** AC neutral-ground bond, made at the **main AC distribution panel**
  (the inverter is a separately-derived source). Single point only.
- The AC equipment ground (EGC) lands on the ground bus separately — that is the
  non-current-carrying safety ground, distinct from the N-G bond.
- **No internal N-G transfer relay**, so that bond is permanent. A generator with
  its **own bonded neutral** would create a second N-G bond during pass-through
  (parallel neutral paths — a hazard, and a noise source). **Use a
  floating-neutral generator** (or unbond it). Confirm the genset before relying
  on the shore/generator charge path.

## 5. Equipment grounding (always required)

Bond every chassis — inverter, both charge controllers, battery enclosure metal,
PV frames — together to the ground bus and out to a **grounding electrode (rod)**.
This is touch-safety and a surge path; it exists regardless of the system-bond
choices above. The inverter has a dedicated DC equipment-ground terminal.

## 6. Conductor sizing

Sizing rule: a conductor that can carry **fault current** is sized to survive the
fault until the OCPD clears, and **scales up when the power cable is oversized**; a
conductor that is only an **earth reference** does not scale.

| Conductor | Sized by | This system |
| --- | --- | --- |
| AC system-bonding jumper (neutral → ground bus) | largest AC hot (Table 2-1) | **#8** |
| Equipment ground, inverter case → ground bus | OCPD rating (Table 2-2), upsized if cable oversized | **~#4** (base #6 @ 175 A, bumped for the 4/0 cable) |
| Grounding electrode conductor, ground bus → rod | earth reference only — **capped** | **#6** (regardless of cable size) |
| DC system-bonding jumper | — | **none** — the Classic GFP is the DC reference (§3) |

## 7. Per-leg overcurrent protection

OCPD goes in the **positive** line (MS4448 manual; "install a DC overcurrent
protection device in the positive DC cable line"). Size to the source's max
continuous output **and** never above the conductor's ampacity.

| Leg | Device | Direction handling | Status / notes |
| --- | --- | --- | --- |
| Battery ↔ MS4448 inverter | **175 A Class-T** fuse, 4/0 cable | bidirectional (fuse is direction-agnostic; manual notes it "can be energized from both directions") | per Table 2-3; confirm/install |
| Battery ↔ EPEver | **TBD — source-dependent** (see note) | bidirectional required | EPEver outputs up to 100 A, but its battery **terminal barely accepts bare 4 AWG**, capping the leg at ~85–95 A. The Heschen 2P 125 A is **rejected** — its terminals (max 16 mm² ≈ 6 AWG) can't even take 4 AWG, and a 125 A trip wouldn't protect 4/6 AWG wire. |
| Battery ↔ Classic | existing **100 A 2-pole disconnect** | confirm: a load-break DC *disconnect* is bidirectional, but verify whether it also provides overcurrent protection — if it's disconnect-only, add a bidirectional OCPD | confirm |
| PV array 0 → Classic | existing **20 A 2-pole disconnect** (PV side) | PV-side, may be polarized | confirm rating |
| Other DC legs (Orion 48/12, heater) | per [wiring.md](wiring.md) | — | heater on a 10 A DC breaker; confirm bidirectionality where on a battery leg |

**EPEver-leg options (final choice depends on what can be sourced in time).** The
leg is bounded by the EPEver's battery terminal (barely accepts bare 4 AWG → ~85–95 A),
so wire it in **4 AWG** and **cap the EPEver BAT Max Charging Current (reg `0x9013`)
to ~60 A** so continuous × 1.25 stays under an 80 A device. Single-pole on the
positive; the negative stays unbroken (GFP reference). Bidirectional ~80 A
protection, in order of preference by what's available:

- **A. Class-T fuse + DC disconnect.** An **80 A Class-T** fuse (~20 kA AIC clears
  the battery's huge short-circuit current) plus a **DC-rated load-break
  disconnect** for isolation. The disconnect only makes/breaks operating current
  (the fuse clears faults), so no high AIC needed — but it must be: voltage rating
  **> 58.4 V** (most marine 48 V battery switches are under-rated for a full LiFePO4),
  ~100 A, load-break (IEC 60947-3 DC-21+), 4 AWG lugs. A disconnect switch is
  inherently bidirectional. Layout: battery + → fuse (close to battery) →
  disconnect → EPEver +.
- **B. MidNite MNEDC80.** Hydraulic-magnetic breaker — natively bidirectional (no
  series-opposed trick needed), serves as its own disconnect, ecosystem-native to
  the Classic, 4 AWG-capable.

## 8. Device / firmware notes

- **Classic 200**, PCB rev 4, firmware **build 2018-02-06** (older than the
  current 2022 build / fw 2193). The dated firmware is why the MNGP arc-fault menu
  didn't match the current manual, and why arc-fault was enabled by a direct
  Modbus write to reg 4187 rather than the menu. A firmware update is worthwhile
  down the line (current menus + arc-fault tuning fixes).

## See also

- [battery-bank.md](subsystems/battery-bank.md) — battery-specific wiring (diagonal parallel takeoff).
- [wiring.md](wiring.md) — canonical physical wiring record.
- [safety.md](safety.md) — top-level safety principles.
- [charge-management.md](charge-management.md) — supervisor status conditions / health endpoint.
