---
title: "Koenig Wildfire — Hardware Setup"
subtitle: "First-boot Pi configuration, wiring, and bring-up checks"
date: "Phase 1 — stub"
---

# Audience and scope

This document is for whoever is **assembling a fresh payload**. By the
end you should have a Pi that:

- Boots Raspberry Pi OS (Bookworm).
- Sees all three IMX296 cameras through the Arducam mux.
- Has the koenig-daemon and koenig-webui services running on boot.
- Joins your wifi if known, falls back to AP mode if not.

If the payload is already built and you just want to use it, you want
`operator_manual.md` instead.

> **Status — Phase 1 (stub).** The sections below outline what we need
> to document. They fill in as Phases 2–5 actually wire up the
> corresponding pieces. Until then, sections marked *TODO* are
> intentionally empty.

# Parts list

| Part | Quantity | Notes |
|---|---|---|
| Raspberry Pi 4 (4 GB or 8 GB) | 1 | Pi 5 not supported yet — see architecture doc for reasoning |
| Arducam Multi Camera Adapter v2.2 | 1 | Also sold as the B0120 4-port board |
| InnoMaker IMX296RAW camera module | 3 | Global-shutter monochrome RAW |
| Narrowband filter — 762 nm | 1 | Off-line reference |
| Narrowband filter — 766 nm | 1 | On-line |
| Narrowband filter — 770 nm | 1 | On-line |
| 3D-printed filter holders | 3 | STLs in `../hardware/stl/` |
| 15-pin CSI ribbon cable | 3 | One per camera, mux↔camera |
| 15-pin CSI ribbon cable | 1 | Pi↔mux |
| USB SSD | 1 | Recommended for sustained timer mode |
| Pi 4 power supply (5 V, 3 A USB-C) | 1 | — |
| MicroSD card (32 GB +) | 1 | Boot disk |

# Pi OS setup

*TODO (filled in during Phase 2 bring-up.)*

Outline:

1. Flash Raspberry Pi OS Bookworm (64-bit) to the SD card with
   Raspberry Pi Imager. Set hostname `koenig-pi`, enable SSH, configure
   your wifi.
2. Boot once with monitor + keyboard attached. Run `sudo raspi-config`
   and enable I²C and the camera interface. Reboot.
3. `sudo apt update && sudo apt full-upgrade`
4. Install dependencies (full list lands in Phase 2):
   `sudo apt install python3-picamera2 python3-flask i2c-tools …`

# Wiring

*TODO with photos. Going to do this with screenshots once the build is
in front of us.*

Outline of what we need to document:

- Pi CSI port → mux CSI in (15-pin ribbon, contacts toward the PCB).
- Mux ports 0/1/2 → each IMX296 camera.
- Mux power (5 V from Pi or separate supply).
- Mux I²C lines (auto-shared with the Pi's I²C bus).
- Mux GPIO select lines → physical pins on the Pi (note the pin
  numbers chosen so the daemon can match them).

![Figure 1: Wiring diagram (placeholder)](img/wiring_diagram.png){ width=80% }

# dtoverlay configuration

*TODO (Phase 3 — when we wire up the mux for real.)*

`/boot/firmware/config.txt` will need:

```
# camera_auto_detect must be off — we're driving the cameras manually.
camera_auto_detect=0

# IMX296 dtoverlay (per camera, with cam0/cam1 selector once mux is in play)
dtoverlay=imx296

# I²C (already enabled via raspi-config, repeat here for clarity)
dtparam=i2c_arm=on
```

The exact mux-aware dtoverlay incantation will be confirmed during
Phase 3 bring-up.

# Bring-up checks

*TODO (Phases 2 and 3.)*

A short script that verifies, in order:

1. `i2cdetect -y 1` shows the mux at its expected address.
2. `libcamera-hello --list-cameras` enumerates exactly one camera (the
   active mux channel).
3. Switching the mux (`tools/check_mux.py select 1`) changes which
   camera the list-command shows.
4. Capturing through each channel produces a non-zero-size file.

This becomes the **acceptance test** for "the payload is assembled
correctly."

# Installing the software

*TODO (Phase 5.)*

Outline:

```bash
git clone git@github.com:ngrabbs/koenig_wildfire.git
cd koenig_wildfire
sudo ./pi/systemd/install.sh   # copies units, enables on boot, starts them
```

After install, browse to `http://koenig-pi.local:8000` (or
`http://192.168.4.1:8000` in AP-fallback mode) and you should see the
UI.

# Wifi configuration

*TODO (Phase 5.)*

The Pi tries known wifi first (configured via Raspberry Pi Imager or
`sudo nmcli con add ...`). If no known network appears within a
timeout, it brings up an AP named `satnet` on `192.168.4.1`
(password `cubesat1`). The legacy setup script in
`../legacy/software/satnet/hostap.sh` is the basis for this.
