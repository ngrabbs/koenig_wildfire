# pi/

## Stored-triplet processing (Phase 4)

Set `PAYLOAD_CALIBRATION_PATH` to the absolute path of
`calibration/correction_20261003.npz`. Stored/simulation processing checks the
archive structure, measured dark flags, finite positive gains, map dimensions,
and records its SHA-256. The hash is provenance, not a permanent allowlist.
Only original uint8 1456x1088 images with ports 0=770, 1=750, 2=780 qualify.
The current live Jetson single-camera path stays skipped/incomplete.

Matching files do **not** prove unchanged focus, orientation, exposure, gain,
or physical optics. Results explicitly report `ACQUISITION_METADATA_UNVERIFIED`
and `PROCESSED_WITH_LIMITATIONS`, never full scientific validity. The supplied
darks were measured at 24000 us; no undocumented exposure tolerance is assumed.

Processing reuses flat-field correction, then registration, then delta77 in
isolated temporary directories. Temporary images are checked and removed;
measurements, hashes, input provenance and registration diagnostics are returned.
`calibrated_region_mask()` excludes ceil(10%) of **each** sensor edge. Masks
are translated into the registered coordinate system and intersected, with a
one-pixel erosion for rounded transform diagnostics/interpolation support.
Clipped raw/corrected pixels are also excluded. No resizing occurs.

Metrics include median/p99 delta77, fraction above 0.05 (an existing descriptive
statistic, **not a fire threshold**), valid counts/fractions, and diagnostic
ratios of channel means over identical valid pixels. `valid_pixel_fraction`
uses the common calibrated/unsaturated region as its denominator;
`valid_full_frame_fraction` uses the full sensor. Decision remains
`NOT_CALIBRATED`: a detection threshold has not been scientifically validated.
Invalid/missing calibration stays `WAITING_FOR_CALIBRATION`; execution or
input failures return `PROCESSING_FAILED` and preserve the captured event.

## Jetson / IHU CAN control (opt-in)

The existing daemon can listen on a selected Linux SocketCAN interface without
creating another camera owner. `PAYLOAD_CAN_INTERFACE` defaults to `vcan0` when
absent; an explicitly empty or whitespace-only value is rejected. CAN remains
disabled unless `PAYLOAD_CAN_ENABLED=1`. On the Jetson/Linux test host, create
the virtual bus:

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

CAPTURE_NOW retains that exact packet and runs one normal shared live capture
cycle using current settings, including `burst_count`. Phase 5 also supports
SET_TIMER_INTERVAL, START_TIMER, STOP_TIMER, and GET_STATUS. Simulation over
CAN is not supported. See [the complete IHU interface](../docs/ihu_jetson_interface.md)
for all bytes and provisional status-service allocation. Run the reference
client with `python3 -m tools.ihu_simulator --interface vcan0`.
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
For a future, independently configured physical interface, select its name with
`PAYLOAD_CAN_INTERFACE=can0` and retain `PAYLOAD_CAN_ENABLED=1`. The daemon does
not discover, create, bring up, or configure interfaces, and does not set bitrate
or fall back to another interface. Startup logs identify the requested interface.
Only `vcan0` validation has been completed; physical CAN and real-IHU validation
remain pending confirmed hardware, wiring, bitrate, and interface details.

A CAN startup `OSError` (including an absent interface or blank name) is logged
with the selected interface and HTTP serving continues. Existing camera and timer
services remain available, including enabled timer scheduling. A receive or
acceptance/completion send `OSError` stops the listener and closes its socket;
HTTP and timer operation continue. There is no automatic reconnect or retry.
After a transport failure or an interface appearing after startup, deliberately
restart the daemon after resolving the cause. `/healthz` is not a CAN readiness
check and may still report `ok=True` when CAN is unavailable.

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
| `PAYLOAD_CAN_ENABLED` | unset | CAN listener starts only when set to `1`. |
| `PAYLOAD_CAN_INTERFACE` | `vcan0` | SocketCAN interface name; blank values are rejected. No interface configuration is performed. |
| `PAYLOAD_STORE`        | `~/payload_images`       | Image storage directory. |
| `PAYLOAD_DAEMON_HOST`  | `127.0.0.1`             | Daemon bind address. |
| `PAYLOAD_DAEMON_PORT`  | `8001`                  | Daemon port. |
| `PAYLOAD_DAEMON_URL`   | `http://127.0.0.1:8001` | UI's view of the daemon. |
| `PAYLOAD_WEBUI_HOST`   | `0.0.0.0`               | UI bind address. |
| `PAYLOAD_WEBUI_PORT`   | `8000`                  | UI port. |
