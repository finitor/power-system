# Troubleshooting

| Symptom | Likely Cause | Checks | Action |
|---|---|---|---|
| No telemetry | Service stopped, bus disconnected, sensor power missing | `systemctl status`, wiring, voltage at sensor | Restart service, inspect wiring |
| Dashboard unreachable | Network issue or dashboard service stopped | Ping Pi, check service status | Restart service or network |
| Implausible voltage/current | Sensor calibration, wrong scaling, loose connection | Compare with handheld meter | Fix calibration or wiring |
| Relay output does not change | GPIO mapping, driver power, failed relay | Check GPIO state and relay input | Disable automation until verified |
| Pi reboots unexpectedly | Power supply sag, SD card issue, overheating | Check 5 V rail and logs | Improve power supply or storage |

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

