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

> **Status — Phase 2 (single-camera).** OS setup, software install,
> and "single camera plugged into the Pi's CSI port" are documented
> and tested. Wiring, mux dtoverlay, three-camera bring-up checks,
> and field wifi land in Phases 3–5.

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

Tested on **Raspberry Pi OS Trixie (Debian 13), 64-bit, Pi 4 (4 GB)**
as of Phase 2.

1. **Flash the SD card** with Raspberry Pi Imager. In the imager's
   advanced settings:
   - Hostname: `koenig-pi`
   - Enable SSH (use public-key auth — paste the key from whoever's
     going to develop against the Pi)
   - Configure your wifi network and country
   - Set user `pi` and a password
2. **First boot.** Plug in monitor + keyboard, or SSH in from your
   laptop:
   ```bash
   ssh pi@koenig-pi.local
   ```
3. **System packages.** SSH'd in, run:
   ```bash
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y git python3-picamera2 python3-flask i2c-tools
   ```
   That gets you everything Phase 2 needs.
4. **Enable I²C** (needed for Phase 3 mux — fine to do now):
   ```bash
   sudo raspi-config nonint do_i2c 0
   sudo reboot
   ```
5. **Camera auto-detect** is on by default in fresh Pi OS images. Verify
   with `grep camera_auto_detect /boot/firmware/config.txt` — should
   show `camera_auto_detect=1` uncommented.

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

```bash
mkdir -p ~/code && cd ~/code
git clone https://github.com/ngrabbs/koenig_wildfire.git
cd koenig_wildfire
sudo bash pi/systemd/install.sh
```

The install script copies the systemd unit files into place, enables
them on boot, starts both services, and prints the URLs to browse to.

Verify everything came up:

```bash
systemctl status koenig-daemon koenig-webui
journalctl -u koenig-daemon -u koenig-webui -n 50 --no-pager
```

Then open `http://koenig-pi.local:8000` from a laptop on the same
network. (Phase 5 adds AP-fallback wifi at `http://192.168.4.1:8000`.)

## Updating the software later

```bash
cd ~/code/koenig_wildfire
git pull
sudo systemctl restart koenig-daemon koenig-webui
```

# Wifi configuration

*TODO (Phase 5.)*

The Pi tries known wifi first (configured via Raspberry Pi Imager or
`sudo nmcli con add ...`). If no known network appears within a
timeout, it brings up an AP named `satnet` on `192.168.4.1`
(password `cubesat1`). The legacy setup script in
`../legacy/software/satnet/hostap.sh` is the basis for this.
