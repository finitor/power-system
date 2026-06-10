# EPEver TEP10425 Integration Notes

Unit on hand 2026-06-10. Manual: `~/Dropbox/manuals/solar/TEP-Manual-EN-V1.1.pdf`
(60 pp, v1.1). Notes here focus on what integration with the supervisor and
the Cubix bank needs; page refs are manual page numbers.

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

## Remote parameter setting (3.3.7)

- "USER" voltage parameters settable via PC software through the **COM
  port** (RJ45) with a USB-to-RS485 cable — our ordered Waveshare adapter's
  job. Also via optional WiFi module + cloud app (not interesting; we are
  local-first).
- The manual contains **no Modbus register map** — EPEver publishes that
  separately (B-series/Tracer-family register doc). Bench task: confirm
  the TEP10425 answers the standard EPEver Modbus RTU registers and which
  are writable.

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
5. Spec-table questions for the dual PV inputs: independent MPPT trackers
   or internally paralleled? Per-input Voc/current limits? (Matters for
   the Classic-failure contingency below.)
6. Record ARM/DSP firmware versions in the inventory notes.
7. Confirm PV-input behavior near the 250 V cold-Voc limit assumption
   against the spec table before wiring the 4s3p array.
