# Tests

Put software tests and hardware simulation tests here.

Run the unit tests with Python's built-in `unittest` runner:

```sh
.venv/bin/python -m unittest discover -s tests
```

Dependencies (`pymodbus`, `magnum-pi`, etc.) are installed on the Pi's venv;
the suite is normally run there.

## What to Test

Test behavior, not markup. The suite was pruned on 2026-06-09 after CSS and
exact-HTML assertions made every cosmetic display change a test-maintenance
chore while catching no real defects.

Worth testing:

- Arithmetic and decode logic that produces plausible-but-wrong values when
  broken: load/autonomy estimates (`test_load.py`), CAN frame decoding,
  charge taper decisions, metric schemas. A human spot-checking the display
  cannot catch a sign error that still prints a believable number.
- Degraded-state rendering: missing CAN, DFU mode, disconnected sensors,
  errors-only snapshots. Displays are spot-checked at deploy time when
  everything is healthy; their failure modes appear when a sensor drops
  overnight, which is precisely what nobody is watching.
- The `/api/v1/snapshot` JSON contract — the terminal console and future
  consumers depend on its keys.
- Behavioral rendering rules (HTML escaping, redundant-state suppression,
  status severity propagation) — asserted on values, not tags.

Not worth testing:

- CSS rules, exact tag structure, column spacing, section ordering, or any
  assertion that fails when the display is intentionally restyled.

## Golden Frames

Whole rendered frames (the terminal snapshot/weather views, the Kindle and
browser pages) are pinned with golden files in `tests/golden/` via
`golden.check_golden(self, name, rendered)` instead of line-by-line assertions.
This keeps a single regression tripwire for layout without making every restyle
a multi-assertion edit — the exact churn the 2026-06-09 pruning was reacting to.
On an intended layout change, re-bless in one command:

```sh
UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_api_terminal_display.py tests/test_web_display.py
```

Goldens are line-end normalized so terminal-width padding stays portable; renders
that depend on wall-clock time must be given a fixed `now=` so the frame is
deterministic. This does **not** replace behavioral tests: HTML escaping, the
never-navigate/refresh-cadence invariants, page-turn routing, degraded-state
rendering, and deliberate row suppressions stay as explicit value assertions
next to the golden check, because a golden re-bless could otherwise reintroduce
them silently.

## Snapshot Factories

`snapshot_helpers.py` provides `make_snapshot()`, `make_classic_telemetry()`,
and `make_battery_snapshot()` with complete defaults. Always build
`SupervisorSnapshot` through the factory in tests — when a field is added to
the snapshot (as with `magnum`), only the factory needs updating, not every
construction site.
