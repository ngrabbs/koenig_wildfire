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

Then browse to `http://payload-pi.local:8000` (or the Pi's IP).

Images land in `~/payload_images/` by default. Override with the
`PAYLOAD_STORE` env var if you want them elsewhere.

## Running as systemd services

```bash
sudo bash pi/systemd/install.sh
```

This copies the unit files to `/etc/systemd/system/`, enables them on
boot, and starts them. The script prints a status block and the URL to
browse to. Live logs:

```bash
journalctl -u payload-daemon -u payload-webui -f
```

To stop:

```bash
sudo systemctl stop payload-daemon payload-webui
```

To uninstall:

```bash
sudo systemctl disable --now payload-daemon payload-webui
sudo rm /etc/systemd/system/payload-{daemon,webui}.service
sudo systemctl daemon-reload
```

## Pulling the latest code on the Pi

```bash
cd ~/code/koenig_wildfire
git pull
sudo systemctl restart payload-daemon payload-webui
```

## Environment variables

| Var | Default | Effect |
|---|---|---|
| `PAYLOAD_STORE`        | `~/payload_images`       | Image storage directory. |
| `PAYLOAD_DAEMON_HOST`  | `127.0.0.1`             | Daemon bind address. |
| `PAYLOAD_DAEMON_PORT`  | `8001`                  | Daemon port. |
| `PAYLOAD_DAEMON_URL`   | `http://127.0.0.1:8001` | UI's view of the daemon. |
| `PAYLOAD_WEBUI_HOST`   | `0.0.0.0`               | UI bind address. |
| `PAYLOAD_WEBUI_PORT`   | `8000`                  | UI port. |
