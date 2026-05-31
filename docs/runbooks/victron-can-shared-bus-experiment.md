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

## Results From May 31, 2026 Battery-Only Survey

The Eco-Worthy app protocol was changed from Pylon to Victron, then the batteries were observed passively from the Raspberry Pi CAN adapter.

Baseline Pylon mode:

- Capture: `data/can-experiments/20260531-111400-cubix-pylon-baseline-500000.log`
- 500 kbit/s produced about 34,000 frames in 10 seconds.
- All observed frames were standard 11-bit CAN IDs.
- Pylon-style frames decoded normally, including charge limits, SOC/SOH, pack voltage/current/temp, status, charge/discharge request flags, manufacturer `PYLON`, cell voltage range, cell temperature range, and installed capacity.

Eco-Worthy app "Victron" mode:

- Captures:
  - `data/can-experiments/20260531-112059-cubix-victron-battery-only-500000.log`
  - `data/can-experiments/20260531-112132-cubix-victron-battery-only-500000.log`
- 250 kbit/s produced only three standard-ID `0xC` frames in each 10 second survey and no useful battery decode.
- 500 kbit/s produced about 29,800 frames in 10 seconds.
- All observed useful frames were standard 11-bit CAN IDs, not 29-bit extended IDs.
- Core battery metrics still decoded through the Pylon-style map: charge limits, SOC/SOH, pack voltage/current/temp, cell voltage range, cell temperature range, and installed capacity.
- The manufacturer field changed from `PYLON` to `ECO-LFP4`.
- The latest-frame decode did not show `0x359` status or `0x35C` charge/discharge request flags during the sampled Victron-mode captures.

Conclusion:

- Eco-Worthy's "Victron" battery protocol setting did not look like VE.Can/NMEA2000 in this battery-only test.
- It looked like a 500 kbit/s managed-battery CAN profile with standard 11-bit IDs and mostly Pylon-compatible data layout.
- A shared physical bus with the BlueSolar's VE.Can/NMEA2000 side remains unlikely unless later controller-only testing shows the BlueSolar can also participate in this 500 kbit/s managed-battery profile.
- The batteries were switched back to Pylon mode after the experiment.

## Useful Commands

Check the adapter:

```sh
.venv/bin/offgrid-can-probe --interface can0
```

Decode a saved Pylon-style capture:

```sh
.venv/bin/offgrid-can-decode --log data/can-experiments/CAPTURE.log --raw
```
