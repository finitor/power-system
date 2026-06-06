# Nginx

`offgrid-supervisor.conf` proxies port 80 to the supervisor web/API server on `127.0.0.1:8080`.

Intended local URLs:

```text
http://blueberry.local/
http://blueberry.local/kindle
http://blueberry.local/api/v1/health
http://blueberry.local/api/v1/snapshot
```

The supervisor should continue to run unprivileged on port 8080. Nginx owns port 80.

Install on Raspberry Pi OS:

```sh
sudo apt install nginx
sudo cp config/nginx/offgrid-supervisor.conf /etc/nginx/sites-available/offgrid-supervisor.conf
sudo ln -sf /etc/nginx/sites-available/offgrid-supervisor.conf /etc/nginx/sites-enabled/offgrid-supervisor.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable --now nginx
```
