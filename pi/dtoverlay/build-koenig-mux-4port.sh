#!/usr/bin/env bash
#
# build-koenig-mux-4port.sh — build our custom multi-camera dtoverlay.
#
# Builds from vendored upstream SOURCE (upstream/), not by decompiling the
# shipped .dtbo. The old approach patched a decompiled binary, which was fine
# for the one-line i2c redirect but cannot add sensor nodes: the __overrides__
# section of a compiled overlay is raw byte arrays, so introducing a new
# sensor means hand-assembling those. Working from source, adding IMX296 is
# an #include and four override lines.
#
# Two changes are applied to upstream (see patch-mux-overlay.py):
#
#   1. i2c redirect. Upstream assumes the PCA954x I2C switch is on the
#      dedicated camera bus (i2c_csi_dsi / Linux i2c-10). The Arducam Multi
#      Camera Adapter v2.2 (B0120) wires it to the GPIO header bus
#      (i2c_arm / i2c-1). Without this, the kernel logs
#      `pca954x 10-0070: probe failed` and no cameras appear at all.
#
#   2. IMX296 support. Upstream ships imx296-overlay.dts for a directly
#      attached IMX296 but no imx296.dtsi for the mux overlays, so IMX296
#      simply is not a sensor a mux port can be set to. ../imx296.dtsi adds
#      it, and the patch script wires it into all four ports.
#
# Usage, on the Pi:
#   sudo bash pi/dtoverlay/build-koenig-mux-4port.sh
#
# Then in /boot/firmware/config.txt, one of:
#   dtoverlay=koenig-mux-4port,cam0-imx477,cam1-imx477,cam2-imx477
#   dtoverlay=koenig-mux-4port,cam0-imx296,cam1-imx296,cam2-imx296
#
# Re-run after a Pi OS update that replaces the stock overlays, and refresh
# upstream/ from rpi-linux if the kernel has moved on (see upstream/README.md).
#
# ---------------------------------------------------------------------------
# IMX296 status — plumbing verified, sensor UNTESTED
#
# Verified on the rig 2026-08-29:
#   - The overlay compiles and is a drop-in replacement: with cam*-imx477 all
#     three IMX477 still enumerate exactly as before.
#   - With cam0-imx296 selected against IMX477 hardware, the kernel logs
#     `imx296 23-001a: invalid device model 0x0000`. That is the driver
#     binding, resolving its clock and regulators, and reading the sensor ID
#     over i2c — everything above the sensor is correct, and it found the
#     wrong chip because an IMX477 is physically installed.
#
# Not yet verified, because it needs the IMX296 plate physically fitted:
#   - Whether the modules run at 37.125 MHz INCK (the default here) or 54 MHz.
#     Those are the ONLY two rates drivers/media/i2c/imx296.c accepts; it
#     rejects anything else at probe. The mux's usual clk_24mhz is unusable.
#     If probe fails with a clock complaint, try 54 MHz:
#         dtoverlay=koenig-mux-4port,cam0-imx296,...,cam0-imx296-clk-freq=54000000
#   - Whether the single CSI data lane negotiates correctly through the mux.
#
# DO NOT MIX SENSOR TYPES. If any configured port fails to probe, the whole
# video-mux media graph fails to register and libcamera reports "No cameras
# available" — including the ports that probed fine. Observed directly: with
# cam0-imx296 (absent) plus cam1/cam2-imx477 (present and probing OK), zero
# cameras enumerated. Set all three ports to the sensor actually installed.
# ---------------------------------------------------------------------------

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must be run as root (sudo)" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM="$HERE/upstream"
OUT="/boot/firmware/overlays/koenig-mux-4port.dtbo"

for tool in dtc cpp python3; do
  command -v "$tool" >/dev/null || {
    echo "$tool not installed. run: sudo apt install -y device-tree-compiler cpp python3" >&2
    exit 1
  }
done

[[ -f "$UPSTREAM/camera-mux-4port-overlay.dts" ]] || {
  echo "vendored upstream source missing: $UPSTREAM/camera-mux-4port-overlay.dts" >&2
  echo "see $UPSTREAM/README.md for how to refresh it." >&2
  exit 1
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Build in a scratch copy so imx296.dtsi sits alongside the upstream .dtsi
# files where the #include can find it, without polluting the repo.
cp -r "$UPSTREAM"/. "$WORK/"
cp "$HERE/imx296.dtsi" "$WORK/"

echo "Patching upstream overlay (i2c redirect + IMX296)"
python3 "$HERE/patch-mux-overlay.py" \
  --src "$WORK/camera-mux-4port-overlay.dts" \
  -o "$WORK/koenig-mux-4port-overlay.dts"

echo "Preprocessing"
( cd "$WORK" && cpp -nostdinc -I. -undef -x assembler-with-cpp \
    koenig-mux-4port-overlay.dts -o preprocessed.dts )

# The duplicate unit-address warnings are expected and also present when
# building upstream unmodified: every sensor the mux supports is declared on
# each port at its own i2c address, and all but the selected one are disabled.
echo "Compiling"
( cd "$WORK" && dtc -@ -H epapr -I dts -O dtb -o koenig-mux-4port.dtbo \
    preprocessed.dts 2> dtc.log ) || { cat "$WORK/dtc.log" >&2; exit 1; }

if grep -v -E "Warning \((unit_address_vs_reg|unique_unit_address)\)" "$WORK/dtc.log" \
     | grep -q "Error"; then
  cat "$WORK/dtc.log" >&2
  exit 1
fi

if [[ -f "$OUT" ]]; then
  BAK="$OUT.bak-$(date +%Y%m%d%H%M%S)"
  cp "$OUT" "$BAK"
  echo "Backed up existing overlay -> $BAK"
fi

install -o root -g root -m 755 "$WORK/koenig-mux-4port.dtbo" "$OUT"
echo "Built $(stat -c '%s' "$OUT") bytes -> $OUT"
echo
echo "Set the sensor in /boot/firmware/config.txt — ALL THREE PORTS THE SAME:"
echo
echo "    dtoverlay=koenig-mux-4port,cam0-imx477,cam1-imx477,cam2-imx477"
echo "  or"
echo "    dtoverlay=koenig-mux-4port,cam0-imx296,cam1-imx296,cam2-imx296"
echo
echo "Then: sudo reboot && rpicam-hello --list-cameras"
