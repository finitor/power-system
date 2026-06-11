# Troubleshooting

| Symptom | Likely Cause | Checks | Action |
|---|---|---|---|
| No telemetry | Service stopped, bus disconnected, sensor power missing | `systemctl status`, wiring, voltage at sensor | Restart service, inspect wiring |
| Dashboard unreachable | Network issue or dashboard service stopped | Ping Pi, check service status | Restart service or network |
| Implausible voltage/current | Sensor calibration, wrong scaling, loose connection | Compare with handheld meter | Fix calibration or wiring |
| Relay output does not change | GPIO mapping, driver power, failed relay | Check GPIO state and relay input | Disable automation until verified |
| Pi reboots unexpectedly | Power supply sag, SD card issue, overheating | Check 5 V rail and logs | Improve power supply or storage |
| Kindle wall display blank/error page | Kindle refresh hit a supervisor restart while bypassing nginx, or Wi-Fi drop | `curl -A 'Kindle/3.0' http://<pi>:8080/` should return 200 even with supervisor stopped | Reload page on Kindle once; verify nginx owns ports 80 and 8080 (see `docs/subsystems/display-services.md`) |
| Desktop console window missing | tmux session recreated while window script could not re-attach; or used `systemctl start` after a manual stop (BindsTo does not propagate starts) | `systemctl is-active offgrid-console`, `tmux list-clients -t offgrid-console` | `sudo systemctl restart offgrid-console`; window re-attaches via `~/.local/bin/open-offgrid-console` loop |
| Console shows stale/missing fields after deploy | Console process running old code against new API | Compare console output to `/api/v1/snapshot` | `sudo systemctl restart offgrid-supervisor` restarts both via BindsTo |
| Magnum values garbled (impossible volts/temps) | magnum-pi CycleTracker joined bus mid-cycle and swapped inverter/remote packets | `magnum-pi sniff`; inverter packet has model byte 0x73 at position 14 | Use `MagnumClient` (identifies by model byte); do not trust raw magnum-pi monitor output |
| Battery CAN silent / stale conditions flapping | gs_usb URB wedge after USB disturbance (adapter up, no frames); loose wire at the adapter screw terminals; or Cubix BMS legitimately quiet at idle | `candump can0` shows nothing; `rx_packets` static in `/sys/class/net/can0/statistics`. **A clean silent bus (zero frames AND zero error counters) cannot distinguish a quiet BMS from an open CAN-H/L wire** — check the screw terminals physically before deeper theories (bit us 2026-06-10) | `offgrid-can-watchdog.timer` auto-resets the adapter within ~2 min (10 min cooldown); if reset doesn't help, reseat the CAN-H/CAN-L screw terminals. Supervisor actor recovers without restart |

## Field Observation Template

```markdown
Date/time:
Weather:
Battery SOC / voltage:
Loads running:
Observed behavior:
Expected behavior:
Photos or logs:
Follow-up:
```

