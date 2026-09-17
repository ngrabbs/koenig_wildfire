#!/usr/bin/env python3
"""Walk the payload through a ladder of exposures and report what each one did.

Runs ON the payload, against the capture daemon on loopback:

    python3 tools/bracket_exposure.py
    python3 tools/bracket_exposure.py -e 500,2000,8000,30000
    python3 tools/bracket_exposure.py -e 1000,4000 -g 1.0,4.0

Narrowband filters cut the light by a large and scene-dependent factor, so the
right exposure is not calculable from the unfiltered one - it has to be
measured in the light you are actually shooting. This takes the guesswork out
of doing that by hand.

The exposure is NOT recorded in the capture filename, so a bracket done by
hand leaves you unable to tell afterwards which frame was which. This writes a
manifest alongside the images mapping every capture back to its settings.

Whatever happens - including Ctrl-C - the original settings are put back.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

DAEMON = "http://127.0.0.1:8001"

# Default ladder in microseconds. Wide on purpose: at these wavelengths the
# sunlit landscape and a flame can sit five stops apart, and the point of a
# bracket is to find that out rather than assume it.
DEFAULT_LADDER = [500, 2000, 8000, 30000]

# A frame is called clipped on the fraction of pixels at or above this. 8-bit
# JPEG, so 254 rather than 255.
CLIP_LEVEL = 254

# Above this fraction of clipped pixels the frame is no use for an index: a
# saturated region has no recoverable value and no gradient to register on.
CLIP_REJECT = 0.5


def get_settings() -> dict:
    r = requests.get(f"{DAEMON}/settings", timeout=10)
    r.raise_for_status()
    return r.json()


def put_shared(patch: dict) -> None:
    r = requests.put(f"{DAEMON}/settings", json={"shared": patch}, timeout=10)
    if r.status_code != 200:
        raise SystemExit(f"settings rejected ({r.status_code}): {r.text.strip()}")


def capture() -> list[dict]:
    r = requests.post(f"{DAEMON}/capture", timeout=120)
    if r.status_code == 409:
        raise SystemExit("payload is busy - stop focus mode and try again")
    r.raise_for_status()
    return r.json()["captures"]


def frame_stats(path: Path) -> dict:
    a = np.asarray(Image.open(path).convert("L"))
    return {
        "mean": float(a.mean()),
        "p99": float(np.percentile(a, 99)),
        "max": int(a.max()),
        "clip": 100.0 * float((a >= CLIP_LEVEL).mean()),
    }


def parse_list(s: str, cast) -> list:
    out = []
    for part in s.split(","):
        part = part.strip()
        if part:
            out.append(cast(part))
    if not out:
        raise argparse.ArgumentTypeError("empty list")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-e", "--exposures", default=",".join(str(x) for x in DEFAULT_LADDER),
                    help="comma-separated exposure times in microseconds")
    ap.add_argument("-g", "--gains", default=None,
                    help="comma-separated analogue gains; every gain is tried "
                         "at every exposure. Default: leave gain alone")
    ap.add_argument("--images", default=str(Path.home() / "payload_images"),
                    help="where the payload writes captures, for the manifest")
    ap.add_argument("--settle", type=float, default=0.3,
                    help="seconds to wait after applying settings (default 0.3)")
    ap.add_argument("-n", "--repeat", type=int, default=1,
                    help="triplets to take at each setting (default 1). Use this "
                         "to collect flats or darks: a median over many frames "
                         "rejects noise, and if you move the payload between "
                         "frames it rejects the scene as well, leaving only the "
                         "fixed pattern of the optics")
    args = ap.parse_args()

    exposures = parse_list(args.exposures, int)
    gains = parse_list(args.gains, float) if args.gains else [None]
    img_dir = Path(args.images)

    before = get_settings()
    shared = before.get("shared", {})
    restore = {"ExposureTime": shared.get("ExposureTime"),
               "AnalogueGain": shared.get("AnalogueGain")}
    restore = {k: v for k, v in restore.items() if v is not None}
    waves = {int(k): int(v) for k, v in before.get("wavelengths", {}).items()}

    if shared.get("AeEnable"):
        print("note: auto-exposure is ON. A bracket is meaningless while the "
              "camera overrides it, and an index computed from auto-exposed "
              "frames is meaningless too. Turning it off for this run.")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest = img_dir / f"bracket_{stamp}.csv"
    rows: list[dict] = []

    print(f"\nbracket of {len(exposures) * len(gains)} settings, "
          f"3 channels each -> {manifest.name}")
    print(f"restoring afterwards: {restore}\n")
    hdr = f"{'exposure':>10} {'gain':>5} {'channel':>12} {'mean':>7} {'p99':>6} {'max':>5} {'clip%':>7}"
    print(hdr)
    print("-" * len(hdr))

    try:
        for gain in gains:
            for exp in exposures:
                patch = {"ExposureTime": int(exp), "AeEnable": False}
                if gain is not None:
                    patch["AnalogueGain"] = float(gain)
                put_shared(patch)
                time.sleep(args.settle)

                g_txt = f"{gain:.1f}" if gain is not None else "-"
                for rep in range(args.repeat):
                    try:
                        caps = capture()
                    except SystemExit as exc:
                        print(f"  {exp:>8} us  capture failed: {exc}")
                        continue

                    frames = []
                    for cap in sorted(caps, key=lambda c: waves.get(c["port"], c["port"])):
                        path = img_dir / cap["id"]
                        if not path.exists():
                            print(f"  missing {cap['id']}")
                            continue
                        st = frame_stats(path)
                        nm = waves.get(cap["port"], cap.get("wavelength_nm", 0))
                        frames.append((cap, nm, st))
                        rows.append({"file": cap["id"], "exposure_us": exp,
                                     "gain": g_txt, "port": cap["port"], "wavelength_nm": nm,
                                     **{k: round(v, 3) for k, v in st.items()}})

                    if args.repeat > 1:
                        # One line per triplet, or this scrolls off the screen
                        means = "  ".join(f"{nm}:{st['mean']:6.2f}" for _, nm, st in frames)
                        worst = max((st["clip"] for _, _, st in frames), default=0.0)
                        flag = "  CLIPPED" if worst >= CLIP_REJECT else ""
                        print(f"{exp:>9}u {g_txt:>5} {f'#{rep + 1}':>5}   {means}{flag}")
                    else:
                        for cap, nm, st in frames:
                            flag = "  CLIPPED" if st["clip"] >= CLIP_REJECT else ""
                            label = f"cam{cap['port']} {nm}nm"
                            print(f"{exp:>9}u {g_txt:>5} {label:>12}"
                                  f" {st['mean']:7.2f} {st['p99']:6.0f} {st['max']:5d}"
                                  f" {st['clip']:6.2f}%{flag}")
                print()
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        if restore:
            put_shared(restore)
            print(f"settings restored: {restore}")
        if rows:
            with manifest.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            print(f"manifest: {manifest}")

    if not rows:
        return 1

    # Recommend the longest exposure that keeps every channel out of clipping.
    # Longest, because more signal is better right up to the point of
    # saturation, and it is saturation that destroys a capture.
    by_setting: dict[tuple, list[dict]] = {}
    for r in rows:
        by_setting.setdefault((r["exposure_us"], r["gain"]), []).append(r)
    clean = [k for k, v in sorted(by_setting.items())
             if all(r["clip"] < CLIP_REJECT for r in v)]
    print()
    if clean:
        exp, gain = max(clean)
        print(f"longest setting with no channel clipping: {exp} us, gain {gain}")
        print("That is the one to keep IF the brightest thing in frame is what "
              "you care about measuring.")
    else:
        print("every setting clipped somewhere - re-run with a shorter ladder, "
              f"e.g. -e {max(1, exposures[0] // 8)},{max(2, exposures[0] // 4)},{max(4, exposures[0] // 2)}")
    print("For a burn: the flame must be unclipped, so pick on the flame and "
          "let the landscape fall dark if it has to.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
