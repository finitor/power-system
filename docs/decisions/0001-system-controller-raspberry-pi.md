# 0001: Use Raspberry Pi as Supervisory Controller

Date: 2026-05-21

## Status

Proposed

## Context

The system needs local telemetry, logging, dashboarding, and limited supervisory control for an off-grid power installation.

## Decision

Use a Raspberry Pi as the supervisory controller. Dedicated power electronics and hardware protection devices remain responsible for critical electrical safety.

## Consequences

- The Pi can host telemetry, dashboard, alerting, and integration services.
- Pi power quality, storage reliability, and reboot behavior must be engineered deliberately.
- Control outputs must default to conservative states if software or Pi power fails.

