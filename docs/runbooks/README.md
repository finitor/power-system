# Runbooks

Action-oriented procedures for installation, commissioning, maintenance, and recovery.

- [Lead-Acid To LiFePO4 Changeover](lead-acid-to-lifepo4-changeover.md): Classic charge-setting changeover and rollback procedure for the Eco-Worthy Cubix 100 battery swap.
- [MagnaSine Charger LiFePO4 Changeover](magnasine-charger-lifepo4-changeover.md): Magnum/MagnaSine inverter-charger setting changeover and rollback planning.
- [Building a New Pi Boot Card](pi-boot-card-build.md): standard procedure for building a new boot microSD for the supervisory Pi on Raspberry Pi OS Lite 64-bit.
- [Escalation Reporting via Healthchecks.io](healthchecks-escalation.md): **LIVE** — off-grid alerts routed through Healthchecks.io as a single notification plane. The `supervisor-degraded` and `supervisor-watchdog` checks are provisioned and emailing (URLs in `/etc/offgrid-power.env`); the dead-man's-switch liveness layer is the one piece still deferred.
- [Adding a Tasmota Sonoff S31](add-tasmota-s31.md): configure, register, verify, and troubleshoot additional individual-load energy monitors.
