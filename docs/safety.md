# Safety

This repository is an engineering aid, not a substitute for electrical code, manufacturer instructions, or qualified review.

## Safety Principles

- Use appropriately rated fuses, breakers, disconnects, wire gauges, terminals, and enclosures.
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

## Emergency Shutdown

Document the exact manual shutdown sequence here once the hardware layout is known.

1. TBD
2. TBD
3. TBD

