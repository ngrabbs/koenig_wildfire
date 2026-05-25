# legacy/

Archived code from the pre-rewrite architecture. **Nothing here is
under active development.** It's preserved as a working reference for
the patterns (histogramming, capture trigger sequencing, AP-mode wifi
setup) that get reused in the new Pi 4 build.

The last working snapshot is tagged `v0.2-his-final-snapshot` — check
that tag out to see the layout as it existed before the move.

## What's in here and where it came from

| Path | Description |
|---|---|
| `firmware/src/main.cpp` | ESP32 flight controller, cleaned version (had compile-time issues with forward decls — see commit history). |
| `firmware/src/main_v0.1.cpp` | Original ESP32 firmware. Working but messier. |
| `software/capture_uplink.py` | Pi-Zero-side script: GPIO trigger → picamera2 capture → JSON meta + histogram over UART → optional JPEG stream on `SEND_IMG`. The histogram + meta logic is the model for the new daemon. |
| `software/ImagingCode2.5.py` | Bench script — manual filter wheel rotation, prompts the user. |
| `software/ImagingCode3.py` | Bench script — pigpio-driven servo auto-rotates the filter wheel at 0°/45°/90°. |
| `software/Alvin_updated_image_processing_code.py` | Offline analysis. Sorts a folder of test images by `760/770/780` in the filename, builds per-channel histograms, writes `histogram data.csv`. |
| `software/c2_server/` | Flask C2 server: `/telemetry`, `/commands`, `/acknowledge`, `/pi_meta`, `/upload_image`. In-memory command queue, no DB. |
| `software/image_dashboard/` | Separate Flask app intended for a Pi wired to GPIO17: capture button, image list, ssh-shutdown of remote Pis. **Closest in spirit to the new UI** — its `templates/index.html` is the design starting point. |
| `software/satnet/` | AP-mode wifi setup using NetworkManager (`nmcli`). SSID `satnet`, password `cubesat1`, AP IP `192.168.4.1`. **Will be ported into `pi/systemd/` in a later phase as the AP fallback for the new build.** |
| `docs_plantuml/` | The old PlantUML architecture + sequence diagrams. Accurate description of the retired stack. New diagrams are mermaid in `docs/architecture.md`. |
| `koenig-his.code-workspace` | Old VS Code multi-root workspace. Points at sibling repos (`bac-hardware`, `electronics-inventory`, `centisat`) — paths may not match current layout. |

## Why retired

The 3-Pi-Zero + ESP32 + LTE architecture worked but was hard to lab:
four separate boards to flash and SSH into, four SD cards to manage,
inter-board UART/GPIO wiring fragility, LTE setup overhead for ground
testing. The new build is a single Pi 4 + Arducam mux + three IMX296
cameras + Flask UI on the operator's laptop browser. See the top-level
`README.md` and `docs/architecture.md`.
