# System Timekeeping and Classic/MNGP Recovery

## Why a fallback is needed

The Raspberry Pi has no hardware RTC (`/dev/rtc` is absent). Under normal
operation, `systemd-timesyncd` obtains accurate time from Internet NTP. Its
persisted clock file only prevents gross backward jumps; it is not a ticking,
battery-backed clock. A hard site power cycle while WAN backhaul is absent can
therefore leave the Pi with an old wall clock, corrupting telemetry timestamps
and local-day calculations.

No usable alternative RTC was found in the Cubix battery telemetry. The EPEver
clock exists but has been unreliable and is not the selected authority.

The MidNite Classic has the best available durable clock:

- the Classic main PCB has a battery-input-powered working clock exposed over
  Modbus;
- the MNGP front panel has a coin-cell-backed RTC (CR1216);
- the Classic's `TMSYNC` mechanism copies MNGP time to the main board.

The origin of the already-correct MNGP time was not proven; it may have been set
by prior commissioning or network-assisted firmware behavior. The recovery
design depends only on the MNGP retaining time through a battery-bank shutdown,
not on how it was originally set.

## Field findings

Classic Modbus registers 4214-4218 (`CTIME0`-`CTIME2`) were read successfully.
Registers 4214-4217 yielded a plausible site-local date and time. Register 4218
(day of year) was stale (`59`) and is deliberately ignored.

The observed Classic clock was about 32 seconds behind the NTP-synchronized Pi,
which is acceptable for outage recovery. Attempts to write `CTIME0` through
external Modbus were acknowledged but immediate readback did not change. On the
installed firmware these registers are effectively read-only, and no documented
Modbus path writes the coin-cell-backed MNGP RTC. Correct the MNGP at its front
panel under **MISC > TIME**, then use the Classic/MNGP time-sync function as
needed.

## Implemented boot sequence

`offgrid-classic-clock-restore.service` runs before
`offgrid-supervisor.service`:

1. Wait up to 15 seconds for the normal NTP synchronized marker.
2. If NTP is unavailable, allow the Classic up to 120 seconds to boot and
   answer Modbus at `192.168.0.10:502`, device ID 10.
3. Reject impossible/default dates (including years before 2020), nonexistent
   DST-local times, naive timestamps, and the unreliable day-of-year field.
4. Require RTC samples separated by at least two seconds that advance at the
   expected rate. A discontinuity restarts confirmation because it may be the
   MNGP copying durable time into the newly booted main board.
5. Recheck NTP on every retry and again before setting the clock.
6. Advance the Pi only when Classic time is more than one second ahead. Never
   move the Pi backward, and reject a forward jump larger than two years.
7. Give up safely if no trustworthy source appears. The service has a hard
   150-second start timeout and the supervisor then starts with the Pi's
   persisted approximate time.

This handles the normal hard-power-cycle race: the Pi and Classic may boot in
either order, and a late MNGP-to-main-board sync is not mistaken for a stable
clock. It also fails open if the Classic is disconnected or never boots.

The helper runs as the unprivileged `offgrid` account with only `CAP_SYS_TIME`.
The long-running supervisor does not receive clock-setting capability.

## Operations and diagnostics

Normal status and boot decision:

```sh
systemctl status offgrid-classic-clock-restore.service
journalctl -u offgrid-classic-clock-restore.service -b --no-pager
```

Expected journal actions include:

- `action=ntp`: Internet NTP won; Classic fallback was unnecessary.
- `action=restored`: the Pi was advanced from the Classic.
- `action=not-ahead`: the Pi's persisted clock was already as new or newer.
- `action=unavailable`: Classic never produced trustworthy time; boot failed
  open and telemetry was released.

Read and evaluate the Classic without changing the Pi:

```sh
sudo .venv/bin/python -m offgrid_power.cli.classic_clock_restore \
  --ignore-ntp --dry-run --ntp-wait-seconds 0 --classic-wait-seconds 5
```

Do not use this helper to discipline clock drift continuously. NTP remains the
normal authority; the Classic is a boot-only recovery source for the specific
case where WAN and a Pi RTC are both unavailable.
