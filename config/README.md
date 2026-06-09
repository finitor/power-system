# Configuration

Configuration templates for the Raspberry Pi and local services.

- `systemd/`: service units.
- `influxdb/`: time-series database setup notes or templates.
- `grafana/`: dashboard exports.
- `nginx/`: reverse proxy config if used.
- `udev/`: USB adapter rules.
- `desktop/`: Pi desktop console window. `open-offgrid-console` deploys to
  `~/.local/bin/` and `offgrid-console.desktop` to `~/.config/autostart/`;
  the autostart entry opens an lxterminal at login that attaches to the
  `offgrid-console` tmux session and re-attaches whenever the console
  service recreates it.

Do not commit secrets. Use `.env` locally.
