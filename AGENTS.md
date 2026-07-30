# Agent operating notes

## Project memory

Durable context — who the operator is, standing working agreements, hardware
findings, and site facts deliberately kept out of this public repo — lives in
`.ai/memory/`, a separate **private** repo of plain Markdown. It is gitignored
here and works with any assistant; nothing about it is tool-specific.

Read `.ai/memory/MEMORY.md` at the start of a session and open the entries
relevant to the task. When you learn something durable that is not recoverable
from the code or git history, add or update an entry there, add a line to that
index, and commit and push it.

If the directory is absent (fresh clone, or a new machine):

```sh
git clone git@github.com:finitor/power-system-memory.git .ai/memory
```

Do not put memory content in this repo: it is public, and the memory holds a
security review, personal details, and the exact site coordinates that
`docs/site.md` intentionally omits.

## Live telemetry SQLite safety

Never open `/srv/telemetry/data/metrics.sqlite` as an account other than
`offgrid`, even with `mode=ro`. A WAL-mode read can create `-wal` and `-shm`
sidecars; files created by another account can block the live supervisor from
writing. Use `sudo -u offgrid` for every live-database query. For long or
repeated analysis, first create a consistent snapshot on the telemetry SSD:

```sh
sudo install -d -o offgrid -g offgrid /srv/telemetry/snapshots
sudo -u offgrid python3 scripts/query_metrics.py snapshot \
  --output /srv/telemetry/snapshots/metrics-snapshot.sqlite
```

Do not put full telemetry snapshots in `/tmp`: it is a small RAM-backed
filesystem on the Pi and the live database is larger than it.

Do not use `immutable=1` against the changing live database.
