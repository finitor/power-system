# Eco-Worthy RS485 / User Companion Software

## Findings (2026-06-10)

A generic FT232R-based USB-to-RS485 cable connects to the Cubix 100 pack's
RS485 port, and Eco-Worthy's Windows "User Companion Software" talks to the
BMS over it:

- **Per-cell voltages and temperatures** are visible in the software's frame
  monitor — richer than the Pylon CAN broadcast, which only carries min/max
  cell voltage with pack:cell locations (frames 0x373–0x375).
- Advanced troubleshooting and maintenance functionality is exposed but
  reportedly behind a **write lock**.
- The frame monitor displays raw transmissions from the battery, which makes
  protocol capture straightforward: run the Companion software with a serial
  sniffer (or capture the same exchange from the Pi with the cable moved
  over) and correlate frames against the displayed values.

## From the official guide (v4.1, `~/Dropbox/manuals/solar/ecoworthy-cubix100-user-companion-sw-guide.pdf`)

- The PC tool is **BMS Tool ES-UP** (observed version ES-UP-V1.22.16),
  Windows-only, downloaded from `app.eco-worthy.com:9008`. It bundles a
  CH340/CH341 USB driver — the official cable is CH340-based, but any
  RS485 dongle works (our FT232R did).
- **Serial settings: 9600 baud** on the BMS service port.
- The pack also has an **RS232 service port**; Eco-Worthy recommends the
  RS232-to-USB cable "since the RS485 port is usually occupied by the
  inverter" — useful if we ever want simultaneous inverter-protocol RS485
  and service access.
- The Monitor page's "Deal Config" reads/sets the inverter-facing
  protocols (RS485 Protocol: PYLON, CAN Protocol: PYLON) — same setting
  the mobile app calls Inverter Protocol Configuration.
- **Premium account password: `666888`** (printed in the official guide,
  section 2.4) — unlocks BMS parameter modification "if you have
  extensive DIY battery experience". The guide warns that modifying BMS
  parameters without Eco-Worthy authorisation voids the warranty.
- Forum reports suggest **recent battery firmware disables the premium
  account/password**; not yet tested against our packs (firmware version
  unknown).
- The mobile app (Bluetooth/Wi-Fi) also shows per-cell voltages and four
  temperature probes (T1-T4) per pack — same data, no PC needed.

## Status of the RS485 path

The earlier judgment (inventory, 2026-06-10) was that battery RS485 is
unnecessary as a *fallback* — CAN telemetry covers supervision. That stands.
What changed: RS485 is now proven as the path to **cell-level granularity**
(all 16 cells per pack, individual temps), which CAN does not broadcast.

Worth reopening only if a concrete need appears, e.g.:

- a weak-cell investigation where min/max + location is not enough;
- charge-taper refinement using full cell-spread shape near the knee;
- validating the BMS's own cell measurements against the ESM-100.

## Possible next steps (unscheduled)

1. Capture a session of frames from the frame monitor alongside the
   displayed per-cell values; derive the request/response protocol (likely a
   Pace/JBD-family BMS dialect — rack LiFePO4 packs of this class usually
   are).
2. If decoded, a Pi-side poller could fetch per-cell arrays on demand —
   but note the port serves one master at a time, and the Companion
   software's write-locked maintenance functions suggest caution about
   what gets transmitted on this bus.
3. Keep the FT232R cable dedicated to bench/diagnostic use; the supervisor's
   operational telemetry stays on CAN.
