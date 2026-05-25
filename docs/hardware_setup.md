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

> **Status — Phase 5b (field-ready).** OS setup, wiring, mux
> dtoverlay, bring-up checks, software install, and AP-fallback wifi
> are documented and tested end-to-end on Trixie + Pi 4 + IMX477 +
> v2.2 mux.

# Parts list

| Part | Quantity | Notes |
|---|---|---|
| Raspberry Pi 4 (4 GB or 8 GB) | 1 | Pi 5 not supported yet — see architecture doc for reasoning |
| Arducam Multi Camera Adapter v2.2 | 1 | Also sold as the B0120 4-port board. Mounts directly on the Pi's GPIO header. |
| Raspberry Pi HQ Camera (IMX477) | 3 | Currently used. InnoMaker IMX296RAW is a global-shutter alternative — see architecture doc. |
| Narrowband filter — 762 nm | 1 | Off-line reference. Bolts onto the lens housing — no wiring. |
| Narrowband filter — 766 nm | 1 | On-line |
| Narrowband filter — 770 nm | 1 | On-line |
| 3D-printed filter holders | 3 | STLs in `../hardware/stl/` |
| 15-pin CSI ribbon cable | 3 | One per camera, mux↔camera |
| 15-pin CSI ribbon cable | 1 | **Pi↔mux** — easy to forget, the HAT does *not* route MIPI through the GPIO header |
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
   sudo apt install -y git python3-picamera2 python3-flask \
                       python3-apscheduler i2c-tools device-tree-compiler
   ```
4. **Enable I²C** (needed for Phase 3 mux — fine to do now):
   ```bash
   sudo raspi-config nonint do_i2c 0
   sudo reboot
   ```
5. **Camera auto-detect** is on by default in fresh Pi OS images. Verify
   with `grep camera_auto_detect /boot/firmware/config.txt` — should
   show `camera_auto_detect=1` uncommented.

# Wiring

The Arducam v2.2 is a **HAT** — it mounts on the Pi's 40-pin GPIO
header and gets I²C (SDA/SCL on pins 3/5), the three GPIO mux select
lines (BCM 4 on pin 7, BCM 17 on pin 11, BCM 18 on pin 12), and 5 V
power automatically through that connector. So a lot of what you'd
expect to wire up is already wired.

The one thing that is **NOT** automatic — and is easy to miss — is the
**CSI ribbon cable from the HAT's CSI output socket back down to the
Pi's CSI input socket**. The HAT has its own CSI output because the
MIPI/CSI signal can't ride along the GPIO header. Without that
ribbon, sensors will enumerate over I²C (so `rpicam-hello
--list-cameras` shows all three) but every capture attempt will hang
with `Device timeout detected`.

> **Ribbon orientation matters at every CSI connector.** Pi side:
> contacts face toward the HDMI ports. HAT side: contacts face the
> HAT PCB (away from the heatsink/jack/etc.). Camera side: contacts
> face the camera PCB. If a ribbon is upside down on any end, you
> get the same "sensors enumerate but capture hangs" symptom as a
> missing ribbon.

Cameras plug into the HAT's three (or four — fourth is unused) input
ports. Order matters only insofar as our daemon maps physical port to
filter wavelength: cam0 → 762 nm, cam1 → 766 nm, cam2 → 770 nm.

![Figure 1: Wiring diagram (placeholder)](img/wiring_diagram.png){ width=80% }

# dtoverlay configuration

Stock Pi OS Trixie ships `camera-mux-4port.dtbo`, designed for newer
Arducam multi-camera HATs. The **v2.2 board wires the PCA9544 I²C
switch to the GPIO-header i2c bus (i2c-1)**, while stock
`camera-mux-4port` expects it on the dedicated camera/display i2c bus
(i2c-10). Using the stock overlay gives you `pca954x 10-0070: probe
failed` and zero cameras.

We ship a small patch script in the repo that produces
`koenig-mux-4port.dtbo` from the stock overlay. Run it once after
first boot, and re-run it after any Pi OS upgrade that might replace
the stock overlay:

```bash
cd ~/code/koenig_wildfire
sudo bash pi/dtoverlay/build-koenig-mux-4port.sh
```

Then `/boot/firmware/config.txt` needs:

```
# turn off auto-detect — it gets confused by the mux
camera_auto_detect=0

# our patched 4-port mux overlay, with all three ports as IMX477
dtoverlay=koenig-mux-4port,cam0-imx477,cam1-imx477,cam2-imx477

# i2c on the GPIO header (where the mux actually lives)
dtparam=i2c_arm=on
```

Reboot and verify with `rpicam-hello --list-cameras` — you should see
three IMX477 entries with paths like
`/base/soc/i2c@7e804000/pca@70/i2c@{0,1,2}/imx477@1a`.

See [`pi/dtoverlay/README.md`](../pi/dtoverlay/README.md) for the
deeper explanation of the patch and a TODO for IMX296 support.

# Bring-up checks

After plugging everything in, booting, building the dtoverlay, and
rebooting, run these in order:

1. **PCA9544 visible on i2c-1.**
   ```bash
   sudo i2cdetect -y 1
   ```
   Should show `70` in the 70-row. If it's missing, the HAT isn't
   seated on the GPIO header properly.

2. **Three cameras enumerated.**
   ```bash
   rpicam-hello --list-cameras
   ```
   Should list three `imx477` entries at
   `/base/soc/i2c@7e804000/pca@70/i2c@{0,1,2}/imx477@1a`.
   If you get zero or fewer than three, check the camera-to-mux
   ribbons and reseat them.

3. **Each camera actually captures.**
   ```bash
   for n in 0 1 2; do
     rpicam-still -n --camera $n -o /tmp/cam$n.jpg --timeout 1500
   done
   ls -lh /tmp/cam*.jpg
   ```
   All three files should be ≥ 500 KB. If `rpicam-still` hangs and
   eventually fails with `Device timeout detected`, the Pi↔HAT CSI
   ribbon is the prime suspect — either missing, loose, or upside
   down on one end.

4. **Daemon runs the burst over HTTP.**
   ```bash
   curl -sX POST http://127.0.0.1:8001/capture
   ```
   Should return a JSON body listing three captures with `port`,
   `wavelength_nm`, `id`, and `bytes` per channel. Takes ~8 seconds.

If all four pass, the payload is assembled correctly.

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

# Wifi configuration (AP fallback)

The Pi joins your known wifi networks first. If none are reachable
(field site with no infrastructure), it falls back to broadcasting
its own WPA2 access point named **`satnet`** (password **`cubesat1`**)
at **`192.168.4.1`**. The operator's laptop joins that and reaches
the UI at `http://192.168.4.1:8000`.

## Install

```bash
sudo bash pi/network/install-ap-fallback.sh
```

The script is idempotent — re-run it any time, including after Pi OS
upgrades. It creates a NetworkManager wifi profile in AP mode with
`autoconnect-priority = -999` and bumps every existing client-wifi
profile to priority `10`, so satnet only activates when no higher
profile can be brought up.

Override the defaults via env:

```bash
sudo SSID=koenig-field PASSWORD=longerthan8chars AP_IP=10.42.0.1 \
     bash pi/network/install-ap-fallback.sh
```

## Add a "known" wifi network

The first time you receive a Pi, give it the wifi network(s) it should
prefer. SSH in and:

```bash
sudo nmcli device wifi connect "<SSID>" password "<PASSWORD>"
```

This creates a profile that NetworkManager auto-connects on every
boot. To preserve the AP-fallback behaviour, the script bumps such
profiles to priority 10 automatically the next time it runs — or you
can do it by hand:

```bash
sudo nmcli con modify "<SSID>" connection.autoconnect-priority 10
```

See [`pi/network/README.md`](../pi/network/README.md) for the test
procedure that doesn't lock you out of the Pi.
