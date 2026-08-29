# upstream/

Vendored device-tree sources from
[raspberrypi/linux](https://github.com/raspberrypi/linux), branch `rpi-6.12.y`,
fetched 2026-08-29. All files keep their original SPDX headers and are
GPL-2.0-only.

These are **unmodified**. `../build-koenig-mux-4port.sh` copies them to a
scratch directory and applies our changes there via
`../patch-mux-overlay.py`, so the diff against upstream stays visible and a
refresh is just a re-fetch.

## Why vendored rather than fetched at build time

The build runs on the Pi, often in the field, and must not depend on network
access. Vendoring also pins a known-good version: a Pi OS update that reshapes
the upstream overlay would otherwise silently change what we build.

## Refreshing

```bash
B=https://raw.githubusercontent.com/raspberrypi/linux/rpi-6.12.y/arch/arm/boot/dts/overlays
cd pi/dtoverlay/upstream
for f in camera-mux-4port-overlay.dts arducam-64mp.dtsi imx219.dtsi imx258.dtsi \
         imx290_327.dtsi imx477_378.dtsi imx519.dtsi imx708.dtsi ov2311.dtsi \
         ov5647.dtsi ov64a40.dtsi ov7251.dtsi ov9281.dtsi; do
  curl -sS -o "$f" "$B/$f"
done
curl -sS -o dt-bindings/gpio/gpio.h \
  https://raw.githubusercontent.com/raspberrypi/linux/rpi-6.12.y/include/dt-bindings/gpio/gpio.h
```

Then re-run the build. `patch-mux-overlay.py` anchors each edit on an exact
string and aborts with a clear message if an anchor has moved, so a breaking
upstream change surfaces as an error rather than a bad overlay.

Match the branch to the kernel you are running (`uname -r`).
