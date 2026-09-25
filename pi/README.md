# pi/

## Jetson CAN proof of concept (opt-in)

The existing daemon can listen on Linux SocketCAN `vcan0` without creating
another camera owner. On the Jetson/Linux test host, create the virtual bus:

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 up
PAYLOAD_CAN_ENABLED=1 python3 -m pi.daemon.main
```

In separate terminals using `can-utils`:

```bash
candump vcan0,320:7FF
cansend vcan0 2A0#0000080101
```

Only that exact standard-ID, classic CAN CAPTURE_NOW request executes. It
runs one normal shared live capture cycle using current settings, including
`burst_count`. No timer/status/simulation commands are implemented.
Replies use node `0x20` / CAN ID `0x320`, following
[SpaceCAN Service 01](https://pypi.org/project/spacecan/0.8.0/):

| Meaning | Reply payload for CAPTURE_NOW |
|---|---|
| Acceptance success | `00 00 01 01 08 01` |
| Acceptance failure | `00 00 01 02 08 01` |
| Completion success | `00 00 01 07 08 01` |
| Completion failure | `00 00 01 08 08 01` |

Acceptance confirms packet validity. Busy or failed captures get acceptance
success followed by completion failure; successful captures get acceptance
success followed by completion success. Unsupported single-frame requests
get acceptance failure echoing their service/subtype. Wrong IDs, flagged
frames, truncated headers, and segmented packets are ignored.

Completion means the capture finished, never fire detection. The shared
result still has `decision=NOT_CALIBRATED`. The listener handles requests
serially; frames received during capture may wait in the kernel buffer.
The camera read has no new timeout or cancellation in this proof of concept.
CAN is disabled unless `PAYLOAD_CAN_ENABLED=1`; if `vcan0` is absent or
SocketCAN unavailable, an error is logged and HTTP remains available.

Tests: `python3 -m unittest pi.tests.test_can_listener -v`. The real SocketCAN
test runs only on Linux with `vcan0` already up; all other tests use fakes.

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
