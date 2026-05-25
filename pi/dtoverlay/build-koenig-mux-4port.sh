#!/usr/bin/env bash
#
# build-koenig-mux-4port.sh — build our custom multi-camera dtoverlay.
#
# Why this exists:
#   Pi OS Trixie ships /boot/firmware/overlays/camera-mux-4port.dtbo, designed
#   for Arducam-style 4-camera multiplexer boards. It assumes the PCA9544 I²C
#   switch is on the Pi's dedicated camera/display i2c bus (`i2c_csi_dsi` →
#   /soc/i2c0mux/i2c@1, Linux bus i2c-10), which is the bus that comes through
#   the CSI ribbon cable.
#
#   The Arducam Multi Camera Adapter v2.2 (the older B0120 board we use)
#   routes the PCA9544 to the GPIO HEADER i2c bus instead (`i2c_arm` →
#   /soc/i2c@7e804000, Linux bus i2c-1). With stock libcamera + stock
#   camera-mux-4port.dtbo, the kernel logs `pca954x 10-0070: probe failed`
#   because no PCA9544 lives on i2c-10.
#
# What this script does:
#   1. Decompile the stock camera-mux-4port.dtbo to source.
#   2. Patch the __fixups__ symbol map: rename `i2c_csi_dsi` -> `i2c_arm`.
#      That redirects the entire mux subtree's parent i2c from the dedicated
#      camera bus to the GPIO header bus, matching where the v2.2 actually
#      wires it.
#   3. Recompile to /boot/firmware/overlays/koenig-mux-4port.dtbo.
#
# To use the result, in /boot/firmware/config.txt:
#   dtoverlay=koenig-mux-4port,cam0-imx477,cam1-imx477,cam2-imx477
# (Same flag-style sensor selection as upstream — IMX477, IMX219, IMX519,
#  IMX708, OV5647, OV7251, OV9281, IMX258, IMX290, OV2311, OV64A40, and
#  arducam-64mp are all supported. IMX296 is NOT — see TODO below.)
#
# Run this on the Pi after every Pi OS update:
#   sudo bash pi/dtoverlay/build-koenig-mux-4port.sh
#
# TODO: add IMX296 support. Upstream doesn't ship an imx296.dtsi, so this
# requires writing one from scratch (based on the standalone imx296-overlay.dts)
# plus adding fragment + __overrides__ entries. Roughly 200 lines of dts.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must be run as root (sudo)" >&2
  exit 1
fi

STOCK="/boot/firmware/overlays/camera-mux-4port.dtbo"
OUT="/boot/firmware/overlays/koenig-mux-4port.dtbo"

if [[ ! -f "$STOCK" ]]; then
  echo "stock overlay not found: $STOCK" >&2
  echo "is this Pi OS Trixie? camera-mux-4port.dtbo should ship by default." >&2
  exit 1
fi

if ! command -v dtc >/dev/null; then
  echo "dtc not installed. run: sudo apt install -y device-tree-compiler" >&2
  exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

DTS="$WORK/decompiled.dts"

echo "Decompiling $STOCK"
dtc -@ -I dtb -O dts "$STOCK" > "$DTS" 2> "$WORK/dtc.stderr" || {
  cat "$WORK/dtc.stderr" >&2
  exit 1
}

echo "Patching: i2c_csi_dsi -> i2c_arm in __fixups__"
# The relevant line looks like:
#     i2c_csi_dsi = "/fragment@200:target:0";
# We want:
#     i2c_arm     = "/fragment@200:target:0";
# Anchor on `:target:0";` to avoid hitting i2c_csi_dsi0 (a different symbol).
if ! grep -qE '^\s*i2c_csi_dsi = "/fragment@200:target:0";' "$DTS"; then
  echo "patch anchor not found — stock dtbo layout may have changed." >&2
  echo "look for the i2c_csi_dsi line in __fixups__ and update this script." >&2
  exit 1
fi
sed -i -E 's|(^\s*)i2c_csi_dsi( = "/fragment@200:target:0";)|\1i2c_arm\2|' "$DTS"

echo "Recompiling -> $OUT"
dtc -@ -I dts -O dtb -o "$OUT" "$DTS" 2> "$WORK/dtc.stderr" || {
  cat "$WORK/dtc.stderr" >&2
  exit 1
}

echo "Built $(stat -c '%s' "$OUT") bytes."
echo
echo "Add this line to /boot/firmware/config.txt (replacing any existing"
echo "dtoverlay=camera-mux-4port or dtoverlay=imx{N} lines):"
echo
echo "    dtoverlay=koenig-mux-4port,cam0-imx477,cam1-imx477,cam2-imx477"
echo
echo "Then: sudo reboot"
