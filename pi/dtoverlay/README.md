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

**IMX296 is NOT supported by upstream camera-mux-4port** (no
`imx296.dtsi` exists in the rpi-linux tree). Adding it is a TODO —
roughly 200 lines of dts, modelled on the standalone
`imx296-overlay.dts`. Until then, IMX296 hardware needs to be swapped
for one of the supported sensors above to work through the mux.
