# Engineering Hardening Plan

Source: senior-review findings, 2026-06-09. Ordered by risk. Update status
markers as items land; move finished items to the Done section with the
commit reference.

Guiding observation from the review: the codebase structure is sound — the
risks are operational. Deploy discipline, reproducibility, and the gap
between "works on the bench today" and "rebuildable in a cold cabin in
February."

## 1. Single deploy action — DONE

One `scripts/deploy.sh` is the only deploy verb: `git pull --ff-only`,
sync configs to system locations, restart services, health-check.

The `offgrid-supervisor-restart.path` watcher is retired: it auto-restarted
the supervisor on every file change, so each rsync during bench iteration
caused a service restart (one of which killed the Kindle display on
2026-06-09). Explicit restart in deploy.sh replaces it.

rsync of individual files remains acceptable only for tight bench iteration,
and the session must end with `deploy.sh` so the Pi checkout returns to git
truth.

## 2. Truthful dependency manifest — DONE

`pyproject.toml` declared only `pymodbus` and `python-can`; the running
system also needs `magnum-pi` (which pulls pydantic and pyserial-asyncio).
Declared with pins. Rebuilding the venv from the repo on fresh hardware must
always work — restore-from-repo is a first-class scenario off-grid.

## 3. Tests for magnum.py packet identification — DONE

`tests/test_magnum.py` uses the exact hex captured live on 2026-06-09 —
the same bytes that exposed magnum-pi's CycleTracker misidentification.
Covers `_find_packets` in both packet orders, full `MagnumSnapshot` field
decode, and the 48V voltage scaling. Gate for any write-path work.

## 4. Stable serial device naming — DONE

udev `SYMLINK+="magnum-rs485"` for the SH-U11H (PL2303 067b:23a3);
supervisor uses `MAGNUM_DEVICE=/dev/magnum-rs485`. Prevents the wrong-bus
failure when a second RS485 adapter changes enumeration order.

## 5. CI — DONE

GitHub Actions workflow runs the unittest suite on every push. Catches
import breaks and contract regressions before code reaches hardware.
Requires item 2 (manifest) so `pip install -e .` brings real deps.

## 6. Per-device readers with staleness — TODO (next major task)

The supervisor poll loop reads Classic (3s timeout), CAN (1.5s window), and
Magnum (serial open + up to ~5s) sequentially every 5s tick. One slow device
starves display freshness and, eventually, control decisions. Planned RS485
charge controllers exceed the budget.

Design sketch:

- One reader thread per device adapter, each maintaining a last-good
  snapshot with `captured_at`.
- Snapshot composer assembles `SupervisorSnapshot` from the caches without
  blocking on any device.
- Staleness becomes explicit: every device payload carries its age; displays
  flag stale data (a 4-minute-old voltage is a different fact from a live
  one, especially once charge-parameter writes depend on it).
- `MagnumClient` holds its serial port open instead of reopening per read;
  replace the `asyncio.run()`-per-read pattern with a persistent reader.

Do this before integrating the next device, not after.

## 7. SD card durability — TODO (background)

With the SSD removed, all telemetry writes land on the SD card: sqlite every
60s, `load-samples.csv` appended every 5s and fully rewritten every 5
minutes by the prune, `ambient.csv` unbounded.

- Enable sqlite WAL mode and `synchronous=NORMAL` for the metrics store.
- Prune `load-samples.csv` via write-temp-then-rename, not in-place rewrite.
- Bound or rotate `ambient.csv`.
- The real fix is restoring dedicated storage; document remount procedure.

## 8. Operator actions — USER

- Decide whether the SSD returns; item 7 sizing depends on it.
- Verify the first GitHub Actions run succeeded (repo is private; no API
  token on the workstation to check from the CLI).
- `offgrid-metrics-export.timer` is disabled on the Pi — confirm whether R2
  export is intentionally manual-only right now.

## Done

- Item 1 (deploy.sh, watcher retired): 2bd373a, 47321ec — verified with a
  live self-updating deploy.
- Item 2 (manifest): 31f71a3 — verified by fresh `pip install -e .` on
  Python 3.12, 118 tests pass; suite now also runs on the Mac.
- Item 3 (magnum tests): 31f71a3 — 9 tests from live-captured 2026-06-09
  packets.
- Item 4 (udev symlink): 5128731 — `/dev/magnum-rs485` live, supervisor
  reading through it.
- Item 5 (CI workflow): 31f71a3 — runs `unittest discover` on every push.
