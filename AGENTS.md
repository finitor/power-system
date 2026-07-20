# Agent operating notes

## Live telemetry SQLite safety

Never open `/srv/telemetry/data/metrics.sqlite` as an account other than
`offgrid`, even with `mode=ro`. A WAL-mode read can create `-wal` and `-shm`
sidecars; files created by another account can block the live supervisor from
writing. Use `sudo -u offgrid` for every live-database query. For long or
repeated analysis, first create a consistent snapshot with:

```sh
sudo -u offgrid python3 scripts/query_metrics.py snapshot \
  --output /tmp/metrics-snapshot.sqlite
```

Do not use `immutable=1` against the changing live database.
