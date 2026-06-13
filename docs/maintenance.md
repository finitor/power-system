# Maintenance

## Routine Checks

| Interval | Task | Notes |
|---|---|---|
| Weekly | Review dashboard for unusual voltage, current, or temperature patterns |  |
| Monthly | Inspect terminals, fuses, and enclosure condition | Power down as appropriate |
| Monthly | Confirm backups are being created | Restore-test periodically |
| Seasonally | Review solar production and battery performance | Compare against expectations |

## Backup

Use `scripts/backup-config.sh` as a starting point. Keep at least one backup off the Pi.

## Restore

Use `scripts/restore-config.sh` as a starting point. Document the exact Pi image, OS version, and package versions once chosen.

## Known-Good State

| Date | Git Commit | Pi Image / OS | Notes |
|---|---|---|---|
| 2026-06-13 | `4db22d5` | Raspberry Pi OS Lite 64-bit, Debian GNU/Linux 13/trixie | 32 GB dry-run microSD in service; former 64 GB card retained as rollback. Reboot validated with `/srv/telemetry` on external SSD, supervisor/console/nginx/timers active, health check green. |

## Follow-Up Observations

- Try a deliberate full-charge/top-balance observation on another sunny day. On 2026-05-31, the Eco-Worthy/Pylon CAN SOC rose to 97% but did not report 100%; the BMS continued to advertise charge enabled and 58.4 V / 200 A charge limits, with no alarms or protections. The Classic spent the afternoon in Float/FloatMppt around 54.7 V and later 53.8-53.9 V, so a future test should intentionally hold a documented absorb target long enough to see what BMS frames change near true full.

## Classic Top-Off Observation

On 2026-06-01, a supervised sunny-day top-off attempt was run against the MidNite Classic and Eco-Worthy/Pylon-style battery bank. The baseline behavior was that the battery often stopped reporting above 96-97% SOC even though the BMS continued to allow charge and reported no protections or alarms.

Temporary Classic settings used for the attempt:

| Setting | Elevated test value | Former baseline | New baseline as of 2026-06-03 |
|---|---:|---:|---:|
| Absorb voltage | 56.0 V | 55.2 V | 55.6 V |
| Float voltage | 55.9 V | 54.0 V | 55.0 V |
| Equalize voltage | 56.0 V | 55.2 V | 55.6 V |
| Absorb time | 3600 s | 300 s | 1950 s |
| Max temp-comp voltage | 56.0 V | 55.2 V | 55.6 V |

Observed result:

- SOC moved from 96% to 99% during the supervised window.
- The Classic remained in Float / MPPT or regulating voltage while holding the higher voltage target.
- Pack current tapered to a few amps near the top.
- Cell voltage stayed in a reasonable top-of-charge range, with observed max cell voltage around 3.49 V.
- No BMS protections or alarms appeared, and charge enable stayed true.
- Cell delta was stable enough for the test, roughly 28 mV during the charge hold, with a later single read around 45 mV after rollback and load transition.

Conclusion: raising the Classic top-end settings can push the pack past the usual 96-97% plateau without immediately upsetting the BMS, at least under direct supervision and good solar conditions. After observing the midpoint settings behave benignly twice, the midpoint was promoted to the normal Classic baseline on 2026-06-03.

More aggressive top-off attempts should still be infrequent, sunny-day only, and supervised until automated stop criteria are implemented. Roll back immediately if the BMS reports any protection or alarm, charge enable drops, max cell voltage climbs uncomfortably, cell delta grows quickly, or SOC reaches 100%. Roll back before leaving the system unattended.
