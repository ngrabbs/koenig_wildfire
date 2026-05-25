# pi/

Code that runs on the Raspberry Pi. Two services — they're meant to be
separate even though they both happen to be Flask apps:

| Service | Listens | Job |
|---|---|---|
| `pi.daemon` | `127.0.0.1:8001` | Owns the camera handle. HTTP API for capture, list, fetch, delete. Loopback-only — operators don't hit it directly. |
| `pi.webui`  | `0.0.0.0:8000`   | The page the operator's browser loads. Talks to the daemon over local HTTP, proxies image bytes through itself. |

## Running for development (no systemd)

In two terminals from the repo root:

```bash
# terminal 1
python3 -m pi.daemon.main

# terminal 2
python3 -m pi.webui.app
```

Then browse to `http://koenig-pi.local:8000` (or the Pi's IP).

Images land in `~/koenig_images/` by default. Override with the
`KOENIG_STORE` env var if you want them elsewhere.

## Running as systemd services

```bash
sudo bash pi/systemd/install.sh
```

This copies the unit files to `/etc/systemd/system/`, enables them on
boot, and starts them. The script prints a status block and the URL to
browse to. Live logs:

```bash
journalctl -u koenig-daemon -u koenig-webui -f
```

To stop:

```bash
sudo systemctl stop koenig-daemon koenig-webui
```

To uninstall:

```bash
sudo systemctl disable --now koenig-daemon koenig-webui
sudo rm /etc/systemd/system/koenig-{daemon,webui}.service
sudo systemctl daemon-reload
```

## Pulling the latest code on the Pi

```bash
cd ~/code/koenig_wildfire
git pull
sudo systemctl restart koenig-daemon koenig-webui
```

## Environment variables

| Var | Default | Effect |
|---|---|---|
| `KOENIG_STORE`        | `~/koenig_images`       | Image storage directory. |
| `KOENIG_DAEMON_HOST`  | `127.0.0.1`             | Daemon bind address. |
| `KOENIG_DAEMON_PORT`  | `8001`                  | Daemon port. |
| `KOENIG_DAEMON_URL`   | `http://127.0.0.1:8001` | UI's view of the daemon. |
| `KOENIG_WEBUI_HOST`   | `0.0.0.0`               | UI bind address. |
| `KOENIG_WEBUI_PORT`   | `8000`                  | UI port. |
