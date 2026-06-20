# Contributing

This repo drives a **live off-grid power system** — a Raspberry Pi supervisor
at a remote cabin that monitors and controls real charge controllers, a battery
bank, and an inverter. Changes here can affect hardware in service, so a little
protocol goes a long way.

Start with [docs/runbooks/running-the-supervisor.md](docs/runbooks/running-the-supervisor.md)
(how the Pi runs and deploys) and [docs/architecture.md](docs/architecture.md)
(the lay of the land).

## The one rule that keeps biting us

**Git is the source of truth; the Pi checkout is disposable.** Develop on the
workstation, commit + push, then deploy on the Pi — that single verb pulls the
repo to git truth, renders config, restarts services, and health-checks:

```sh
ssh <user>@blueberry.local 'cd power-system && bash scripts/deploy.sh'
```

Never leave an edit stranded on the Pi. A quick bench fix or a one-off `scp`
made directly on the Pi is **marooned** until it is committed and pushed from
the workstation — and a clean `git pull` will happily ignore an untracked file
there forever. So:

- **Detect:** `scripts/diag.sh` (the first move for anything) prints a `git:`
  line — `clean`, or `DIRTY (N modified, M untracked)`.
- **Reconcile before discarding:** the change may exist *only* on the Pi. Copy
  anything real back into the repo, commit, and push *before* you run
  `git checkout -- .` / `git clean`.

Full detail: [Marooned changes](docs/runbooks/running-the-supervisor.md#marooned-changes-the-rule-that-keeps-biting-us).

## Before you push

- **Run the tests:** `.venv/bin/python -m pytest` (the deploy reruns them on the
  Pi and aborts if they fail).
- **Keep secrets out of git** — site config and keys live in
  `/etc/offgrid-power.env` on the Pi; `.env.example` is the in-repo template.
- **Match the surrounding style**, and keep the operating principles in the
  [README](README.md#operating-principles) in mind: monitoring code may fail
  quietly, but control code must fail conservatively.

## Documentation conventions

- **Decisions** (`docs/decisions/`) and the **journal** (`docs/journal/`) are
  append-only history — don't rewrite past entries; add new ones.
- Everything else (subsystem docs, runbooks, README) should track current
  reality. If you change behavior, update the doc in the same change.
