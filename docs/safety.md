# Safety

This repository is an engineering aid, not a substitute for electrical code, manufacturer instructions, or qualified review.

## Safety Principles

- See **[DC Protection and Grounding](protection-and-grounding.md)** for the overcurrent-protection and grounding/bonding scheme (bidirectional battery-leg breakers, the Classic-GFP ground reference, AC bonding, conductor sizing).
- Use appropriately rated fuses, breakers, disconnects, wire gauges, terminals, and enclosures.
- Battery-leg breakers must be **bidirectional** (charge and fault currents flow opposite directions); a polarized DC breaker can fail to clear a reverse-direction fault.
- Keep high-current wiring physically separated from low-voltage signal wiring where practical.
- Assume batteries can deliver dangerous fault current.
- Prefer dedicated hardware safety devices for critical protection.
- Treat Pi-mediated control as supervisory, not as the only safety layer.
- Document manual shutdown and recovery procedures clearly.

## Critical Limits

| Item | Limit | Source | Notes |
|---|---:|---|---|
| Battery minimum voltage | TBD | Datasheet / BMS manual |  |
| Battery maximum charge voltage | TBD | Datasheet / BMS manual |  |
| Maximum charge current | TBD | Datasheet / system design |  |
| Maximum discharge current | TBD | Datasheet / system design |  |
| Inverter continuous power | TBD | Datasheet |  |

## Manual Shutdown

For the ordered normal-operation shutdown and startup sequences, see
[System Power Procedures](runbooks/system-power-procedure.md).

An emergency-specific shutdown procedure has not yet been documented. Do not
assume that the normal staged shutdown is appropriate for every electrical,
battery, or fire emergency.
