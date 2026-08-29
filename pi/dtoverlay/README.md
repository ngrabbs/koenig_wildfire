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

**Clock — this is the one that cost the most time.**
`drivers/media/i2c/imx296.c` accepts exactly two INCK rates, 37.125 MHz and
54 MHz, and rejects anything else at probe, so the mux's usual `clk_24mhz`
is unusable. We default to **54 MHz**, which is what the InnoMaker
CAM-IMX296RAW's onboard oscillator runs at.

The failure mode when this is wrong is deeply misleading. The declared rate
is only ever a *declaration*: `clk_imx296` is a `fixed-clock`, so it tells
the driver what to assume and generates nothing. The module carries its own
crystal. Declare the wrong rate and the driver programs its INCKSEL
registers for a frequency the silicon isn't running at — i2c keeps working
perfectly (the driver reads the model ID and per-sensor temperature), the
media graph negotiates cleanly, and then **no MIPI data ever arrives**:

```
Camera frontend has timed out!
Please check that your camera sensor connector is attached securely.
```

Nothing anywhere points at the clock. We chased cables and lane counts for
a long time before the rate turned out to be it. If a future module wants
37.125 MHz instead:

```
dtoverlay=koenig-mux-4port,cam0-imx296,...,cam0-imx296-clk-freq=37125000
```

**Host XCLK is not needed.** The mux overlay leaves the Pi's `cam0_clk` /
`cam1_clk` generators disabled, and that is correct — these modules are
self-clocked. Enabling `cam1_clk` was tried and changes nothing.

**Status: working.** Verified end to end on 2026-08-29 with three InnoMaker
CAM-IMX296RAW modules on the mono plate — all three enumerate as
`imx296 [1456x1088 10-bit MONO]`, capture through the daemon at ~0.45 s for
the three-channel cycle, and live focus streams. No extra flags needed
beyond `camN-imx296`.

Two lane bugs had to be fixed to get there, both of which upstream leaves
unhandled for any 1-lane sensor on this mux:

- `mux_inN` defaults to `data-lanes = <1 2>` with a dormant 1-lane variant
  nothing activates. `camN-imx296` now toggles it (`+100-101` per port),
  the same way upstream's `cam0-ov7251` does.
- `fragment@201` hardcodes `csi1_ep` to `data-lanes = <1 2>` and upstream
  offers no override at all, so unicam itself stayed at two lanes. Added
  `fragment@210` as a dormant 1-lane variant, numbered above 201 so it
  applies later, activated by the same override.

> **Do not mix sensor types.** If any configured port fails to probe, the
> whole video-mux media graph fails to register and libcamera reports "No
> cameras available" — including ports that probed fine. Observed directly:
> `cam0-imx296` (absent) alongside `cam1`/`cam2-imx477` (present, probing OK)
> enumerated zero cameras. Set all three ports to the sensor actually fitted.
