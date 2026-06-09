# Nginx

`offgrid-supervisor.conf` proxies ports 80 and 8080 to the supervisor web/API server on `127.0.0.1:8081`.

Intended local URLs:

```text
http://blueberry.local/
http://blueberry.local/kindle
http://blueberry.local/api/v1/health
http://blueberry.local/api/v1/snapshot
```

The supervisor runs unprivileged on port 8081. Nginx owns ports 80 and 8080 — the
8080 listener exists because the Kindle wall display is bookmarked to
`http://blueberry.local:8080/` from before nginx fronted the supervisor; serving
that port from nginx gives the Kindle the auto-retry page during supervisor
restarts instead of a dead browser error page. Local consumers that want the raw
supervisor (for example the terminal console's API poll) should use
`127.0.0.1:8081` directly.

Install on Raspberry Pi OS:

```sh
sudo apt install nginx
sudo cp config/nginx/offgrid-supervisor.conf /etc/nginx/sites-available/offgrid-supervisor.conf
sudo ln -sf /etc/nginx/sites-available/offgrid-supervisor.conf /etc/nginx/sites-enabled/offgrid-supervisor.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable --now nginx
```
