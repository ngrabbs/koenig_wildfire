# pi/dtoverlay/

Custom Raspberry Pi device-tree overlays that aren't available stock on
Pi OS, or that need patching to match our hardware.

## koenig-mux-4port

Wraps the stock `camera-mux-4port.dtbo` with one critical fix: redirect
the PCA9544 I²C switch's parent bus from the dedicated camera/display
i2c (`i2c_csi_dsi` / Linux i2c-10) to the GPIO header i2c
(`i2c_arm` / Linux i2c-1). This matches where the **Arducam Multi
Camera Adapter v2.2** (B0120) actually wires the mux.

Without this fix, the stock overlay produces:

```
[    7.358470] pca954x 10-0070: probe failed
```

and zero cameras come up.

### Build + install

On the Pi:

```bash
sudo bash pi/dtoverlay/build-koenig-mux-4port.sh
```

This decompiles the stock `.dtbo`, applies the one-line patch, and
recompiles to `/boot/firmware/overlays/koenig-mux-4port.dtbo`. Then
edit `/boot/firmware/config.txt`:

```
dtoverlay=koenig-mux-4port,cam0-imx477,cam1-imx477,cam2-imx477
```

and reboot.

### Re-run after Pi OS updates

The stock `camera-mux-4port.dtbo` ships with `raspi-firmware`; if
`apt upgrade` replaces it, your custom build doesn't update with it.
Re-run the build script after every Pi OS upgrade.

### Supported sensors

Whatever upstream `camera-mux-4port` supports — currently IMX219,
IMX258, IMX477, IMX519, IMX708, OV2311, OV5647, OV64A40, OV7251,
OV9281, IMX290, and Arducam-64MP. Selected per port via flag params
(`cam0-imx477`, `cam1-imx219`, etc.).

### IMX296

**Added here** (2026-08-29). Upstream `camera-mux-4port` still has no
`imx296.dtsi`, so IMX296 is not a sensor its ports can be set to.
[`imx296.dtsi`](imx296.dtsi) supplies one and
[`patch-mux-overlay.py`](patch-mux-overlay.py) wires it into all four ports.

Select it the same way as any other sensor:

```
dtoverlay=koenig-mux-4port,cam0-imx296,cam1-imx296,cam2-imx296
```

Three things differ from the other sensors on this mux, and they are why
copying `imx477_378.dtsi` does not work: the clock is named `inck` not
`xclk`, the supplies are `avdd`/`dvdd`/`ovdd` not `VANA`/`VDIG`/`VDDL`, and
it runs **one** CSI data lane at 594 MHz rather than two.

**Clock.** `drivers/media/i2c/imx296.c` accepts exactly two INCK rates,
37.125 MHz and 54 MHz, and rejects anything else at probe — the mux's usual
`clk_24mhz` is unusable. We default to 37.125 MHz (reusing the rate upstream
already provides for IMX290). If probe fails complaining about the clock,
the modules are 54 MHz parts:

```
dtoverlay=koenig-mux-4port,cam0-imx296,...,cam0-imx296-clk-freq=54000000
```

**Status: plumbing verified, sensor untested.** With IMX477 hardware still
fitted and `cam0-imx296` selected, the kernel logs:

```
imx296 23-001a: invalid device model 0x0000
```

That is the driver binding, resolving its clock and regulators, and reading
the sensor ID over i2c. Everything above the sensor is correct; it found the
wrong chip because an IMX477 is physically installed. Confirming the rest
needs the IMX296 plate fitted.

> **Do not mix sensor types.** If any configured port fails to probe, the
> whole video-mux media graph fails to register and libcamera reports "No
> cameras available" — including ports that probed fine. Observed directly:
> `cam0-imx296` (absent) alongside `cam1`/`cam2-imx477` (present, probing OK)
> enumerated zero cameras. Set all three ports to the sensor actually fitted.
