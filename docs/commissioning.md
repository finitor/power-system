# Commissioning

## Pre-Power Checklist

- [ ] Wiring reviewed against `docs/wiring.md`.
- [ ] Cable gauges and fuses match expected current.
- [ ] Polarity verified at every DC connection.
- [ ] Raspberry Pi power supply verified before connecting the Pi.
- [ ] Relays/contactors verified with loads disconnected.
- [ ] Manual overrides verified.
- [ ] Network access verified.
- [ ] Backup and restore procedure tested.

## First Power-Up

| Step | Expected Result | Actual Result | Date | Notes |
|---|---|---|---|---|
| Power Raspberry Pi only | Pi boots and network is reachable |  |  |  |
| Start telemetry service | Sensor readings appear |  |  |  |
| Enable dashboard | Local dashboard loads |  |  |  |
| Test alert path | Test alert received |  |  |  |
| Test each controlled output without load | Output changes state as commanded |  |  |  |
| Test controlled outputs with load | Output works under real conditions |  |  |  |

## Acceptance Criteria

- [ ] Measurements are plausible compared with a handheld meter or device display.
- [ ] Logs persist across reboot.
- [ ] Services restart after Pi reboot.
- [ ] Control outputs default to conservative states after software failure.
- [ ] The system can be operated manually if the Pi is offline.

