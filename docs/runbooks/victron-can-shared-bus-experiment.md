# Victron CAN Shared Bus Experiment

Goal: learn whether the Eco-Worthy Cubix 100 batteries in "Victron" CAN mode can share a physical CAN bus with the Victron BlueSolar MPPT 150/85 CAN-bus controller.

This is an observation-first experiment. Keep the adapter in listen-only mode unless we explicitly need an active CAN participant.

## Known Constraints

- The current battery telemetry works in Pylon-compatible mode at 500 kbit/s using 11-bit CAN IDs.
- The BlueSolar MPPT 150/85 CAN-bus manual describes RJ45 CAN connectors, VE.Can parallel operation, and NMEA2000 protocol.
- NMEA2000 commonly uses 29-bit extended CAN IDs at 250 kbit/s.
- A shared bus is only plausible if all devices use the same bitrate and compatible framing/protocol expectations.
- The CAN bus needs exactly two 120 ohm terminators, one at each physical end. The DSD TECH adapter R120 switch should be enabled only when the adapter is one end of the bus.

## Baseline: Current Battery Mode

Before changing the Eco-Worthy app, capture the known-good Pylon-compatible traffic:

```sh
sudo .venv/bin/offgrid-can-survey --interface can0 --bitrates 500000 --seconds 10 --label cubix-pylon-baseline
```

Expected:

- Standard 11-bit IDs such as `0x351`, `0x355`, `0x356`, `0x359`, `0x35C`, and `0x35E`.
- Pylon-style summary lines for charge limits, SOC, pack voltage/current/temp, and requests.

## Battery-Only Victron Mode Survey

After switching the Cubix CAN protocol to Victron in the Eco-Worthy app, wait 10 seconds and run:

```sh
sudo .venv/bin/offgrid-can-survey --interface can0 --bitrates 250000,500000 --seconds 10 --label cubix-victron-battery-only
```

Interpretation:

- Frames only at 250 kbit/s, mostly extended IDs: likely VE.Can/NMEA2000-like.
- Frames only at 500 kbit/s, mostly standard IDs: likely managed-battery CAN, not the same bus as a VE.Can/NMEA2000 controller.
- Frames at both bitrates are not expected. Re-run to confirm before trusting that result.
- No frames at either bitrate means the battery may not transmit without a counterpart, the app setting did not stick, or wiring/termination changed.

## Charge-Controller-Only Survey

When the Victron controller is physically available, survey it by itself before combining buses:

```sh
sudo .venv/bin/offgrid-can-survey --interface can0 --bitrates 250000,500000 --seconds 10 --label victron-controller-only
```

The shared-bus attempt should wait until the battery-only and controller-only captures show the same bitrate.

## Shared-Bus Trial Criteria

Proceed only if:

- Both devices produce traffic at the same bitrate.
- The bus has two terminators total.
- The survey command sees frames from both devices without interface errors or bus-off behavior.
- The supervisor can still read the battery values after restoring the selected operating protocol.

Stop and separate the devices if:

- The adapter reports bus-off or repeated errors.
- Battery telemetry disappears unexpectedly.
- The controller or batteries report alarms.
- The batteries stop charge/discharge unexpectedly.

## Useful Commands

Check the adapter:

```sh
.venv/bin/offgrid-can-probe --interface can0
```

Decode a saved Pylon-style capture:

```sh
.venv/bin/offgrid-can-decode --log data/can-experiments/CAPTURE.log --raw
```
