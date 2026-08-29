#!/usr/bin/env python3
"""Turn upstream's camera-mux-4port overlay into koenig-mux-4port.

Reads upstream/camera-mux-4port-overlay.dts, applies three changes, and
writes a build-ready .dts. Every edit is anchored on an exact string and
fails loudly if the anchor is gone, so a Pi OS update that reshapes the
upstream file produces a clear error rather than a silently wrong overlay.

The three changes:

1. Reparent the mux from the dedicated camera i2c bus to the GPIO header
   bus. Upstream assumes the PCA954x sits on i2c_csi_dsi (Linux i2c-10),
   which is where it lives on Arducam's newer boards. The Multi Camera
   Adapter v2.2 (B0120) we use wires it to i2c_arm (Linux i2c-1) instead.
   Without this the kernel logs `pca954x 10-0070: probe failed` and no
   cameras appear at all.

2. Add a 37.125 MHz clock node for the IMX296.

3. Add IMX296 to all four ports: the per-port sensor node, the
   camN-imx296 selection override, and a camN-imx296-clk-freq override
   for switching the declared INCK rate to 54 MHz.

Why (3) is needed at all: upstream ships imx296-overlay.dts for a
directly-attached IMX296 but no imx296.dtsi for the mux overlays, so
IMX296 is simply not one of the sensors a mux port can be set to.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sub_once(text: str, old: str, new: str, what: str) -> str:
    n = text.count(old)
    if n != 1:
        sys.exit(
            f"patch-mux-overlay: expected exactly 1 occurrence of the anchor "
            f"for '{what}', found {n}.\n"
            f"Upstream camera-mux-4port-overlay.dts has probably changed shape. "
            f"Re-check the anchor:\n    {old.strip()[:120]}"
        )
    return text.replace(old, new)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path,
                    default=HERE / "upstream" / "camera-mux-4port-overlay.dts")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()

    if not args.src.is_file():
        sys.exit(f"upstream source not found: {args.src}")
    dts = args.src.read_text()

    # --- 1. mux lives on the GPIO header i2c, not the camera i2c ----------
    dts = sub_once(
        dts,
        "\ti2c_frag: fragment@200 {\n\t\ttarget = <&i2c_csi_dsi>;",
        "\ti2c_frag: fragment@200 {\n"
        "\t\t/* koenig: Arducam Multi Camera Adapter v2.2 (B0120) wires the\n"
        "\t\t * PCA954x to the GPIO header i2c (i2c-1), not the camera i2c\n"
        "\t\t * (i2c-10) that upstream assumes. */\n"
        "\t\ttarget = <&i2c_arm>;",
        "i2c_csi_dsi -> i2c_arm",
    )

    # --- 2. clock node for the IMX296 ------------------------------------
    # imx296.c accepts only 37.125 MHz and 54 MHz as INCK. Default to
    # 37.125; camN-imx296-clk-freq switches it.
    dts = sub_once(
        dts,
        "\t\t\tclk_imx290: clk_imx290 {",
        "\t\t\tclk_imx296: clk_imx296 {\n"
        "\t\t\t\tcompatible = \"fixed-clock\";\n"
        "\t\t\t\t#clock-cells = <0>;\n"
        "\n"
        "\t\t\t\t/* imx296.c accepts 37.125 MHz or 54 MHz only.\n"
        "\t\t\t\t * 54 MHz is what the InnoMaker CAM-IMX296RAW's onboard\n"
        "\t\t\t\t * oscillator runs at - see the note in the build script. */\n"
        "\t\t\t\tclock-frequency = <54000000>;\n"
        "\t\t\t\tstatus = \"okay\";\n"
        "\t\t\t};\n"
        "\n"
        "\t\t\tclk_imx290: clk_imx290 {",
        "clk_imx296 node",
    )

    # --- 3a. per-port sensor nodes ---------------------------------------
    # Append after each port's ov64a40 block, which is the last sensor in
    # every i2c@N and so a stable insertion point.
    for port in range(4):
        anchor = (
            f"\t\t\t\t\t#define cam_node ov64a40_{port}\n"
            f"\t\t\t\t\t#define cam_endpoint ov64a40_{port}_ep\n"
            f"\t\t\t\t\t#define vcm_node ov64a40_{port}_vcm\n"
            f"\t\t\t\t\t#define cam1_clk clk_24mhz\n"
            f"\t\t\t\t\t#include \"ov64a40.dtsi\"\n"
            f"\t\t\t\t\t#undef cam_node\n"
            f"\t\t\t\t\t#undef cam_endpoint\n"
            f"\t\t\t\t\t#undef vcm_node\n"
            f"\t\t\t\t\t#undef cam1_clk\n"
        )
        addition = (
            anchor
            + f"\n"
            f"\t\t\t\t\t/* koenig: IMX296 - not shipped upstream for the mux */\n"
            f"\t\t\t\t\t#define cam_node imx296_{port}\n"
            f"\t\t\t\t\t#define cam_endpoint imx296_{port}_ep\n"
            f"\t\t\t\t\t#define cam1_clk clk_imx296\n"
            f"\t\t\t\t\t#include \"imx296.dtsi\"\n"
            f"\t\t\t\t\t#undef cam_node\n"
            f"\t\t\t\t\t#undef cam_endpoint\n"
            f"\t\t\t\t\t#undef cam1_clk\n"
        )
        dts = sub_once(dts, anchor, addition, f"imx296 node on port {port}")

    # --- 3c. one-lane variant for the CSI receiver ------------------------
    # fragment@201 hardcodes csi1_ep to data-lanes = <1 2>, and upstream
    # offers no way to change it. Setting the mux inputs to one lane is not
    # enough on its own: unicam itself stays configured for two and waits
    # for a second lane the IMX296 never drives. Numbered 210 so it applies
    # after 201 and wins.
    dts = sub_once(
        dts,
        "\tfragment@202 {\n\t\ttarget = <&i2c0if>;",
        "\t/* koenig: 1-lane CSI receiver, activated by camN-imx296 */\n"
        "\tfragment@210 {\n"
        "\t\ttarget = <&csi1_ep>;\n"
        "\t\t__dormant__ {\n"
        "\t\t\tdata-lanes = <1>;\n"
        "\t\t};\n"
        "\t};\n"
        "\n"
        "\tfragment@202 {\n\t\ttarget = <&i2c0if>;",
        "csi1_ep 1-lane fragment",
    )

    # --- 3b. selection overrides -----------------------------------------
    # Mirrors the camN-imx477 pattern: cross-link the sensor endpoint and
    # the mux input, then enable the node.
    # The IMX296 is a ONE-lane sensor. mux_inN defaults to data-lanes = <1 2>
    # (fragment@101/103/105/107) with a dormant 1-lane variant
    # (fragment@100/102/104/106). Without flipping those, the CSI receiver
    # waits for a second lane that never arrives: the sensor probes fine over
    # i2c, the media graph negotiates cleanly, and then every capture dies
    # with "Camera frontend has timed out". Upstream's only 1-lane sensor on
    # this mux, ov7251, does exactly this. Port N toggles 100+2N / 101+2N.
    # +210 additionally drops the CSI receiver itself to one lane; without
    # that, unicam waits for a second lane and the capture still times out.
    overrides = "\n".join(
        f"\t\tcam{p}-imx296 = <&mux_in{p}>, \"remote-endpoint:0=\",<&imx296_{p}_ep>,\n"
        f"\t\t\t      <&imx296_{p}_ep>, \"remote-endpoint:0=\",<&mux_in{p}>,\n"
        f"\t\t\t      <&mux_in{p}>, \"clock-noncontinuous?\",\n"
        f"\t\t\t      <&imx296_{p}>, \"status=okay\",\n"
        f"\t\t\t      <0>,\"+{100 + 2 * p}-{101 + 2 * p}+210\";"
        for p in range(4)
    )
    # Retunes the rate declared to the driver. The module carries its own
    # oscillator, so this must match the crystal on the board - it does not
    # program anything on the Pi.
    clk_overrides = "\n".join(
        f"\t\tcam{p}-imx296-clk-freq = <&clk_imx296>,\"clock-frequency:0\";"
        for p in range(4)
    )
    dts = sub_once(
        dts,
        "\t\tcam0-imx290-clk-freq = <&clk_imx290>,\"clock-frequency:0\",",
        "\t\t/* koenig: IMX296 selection + INCK rate */\n"
        + overrides + "\n\n"
        + clk_overrides + "\n\n"
        "\t\tcam0-imx290-clk-freq = <&clk_imx290>,\"clock-frequency:0\",",
        "imx296 overrides",
    )

    args.out.write_text(dts)
    print(f"wrote {args.out} ({len(dts.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
