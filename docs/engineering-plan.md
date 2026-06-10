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

## 6. Per-device actor threads with staleness — PHASE 1 DONE

The supervisor poll loop read Classic (3s timeout), CAN (1.5s window), and
Magnum (serial open + up to ~5s) sequentially every 5s tick. One slow device
starved display freshness and, eventually, control decisions.

Phase 1 (done): `offgrid_power/readers.py` provides `PollingReader`, a
device actor — one thread owns each adapter exclusively. Reads happen on its
poll loop; writes are submitted to the same thread as queued commands
(`PollingReader.submit`), so reads and writes to one device can never race.
The charger taper writes via `Supervisor.write_classic_charge_settings`,
which routes through the classic actor. Failures keep the last good value;
stale readings (default 4x interval) surface as WARNING status conditions in
every display. Enabled by default; `--no-device-readers` restores the
synchronous path, and `--once` always reads synchronously.

Phase 2 (TODO):

- Persistent Magnum listener: hold the serial port open and consume bus
  cycles continuously instead of open/read/close per poll. Natural home for
  the future remote-takeover transmit loop (decision 0002), which must emit
  the remote packet every ~100ms on the same thread that owns the port.
- Surface per-device ages in the API payload (staleness is currently only a
  status condition).

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

- Item 1 (deploy.sh, watcher retired): e4444fc, a6f506f — verified with a
  live self-updating deploy.
- Item 2 (manifest): 936512a — verified by fresh `pip install -e .` on
  Python 3.12, 118 tests pass; suite now also runs on the Mac.
- Item 3 (magnum tests): 936512a — 9 tests from live-captured 2026-06-09
  packets.
- Item 4 (udev symlink): e04ba65 — `/dev/magnum-rs485` live, supervisor
  reading through it.
- Item 5 (CI workflow): 936512a — runs `unittest discover` on every push.
