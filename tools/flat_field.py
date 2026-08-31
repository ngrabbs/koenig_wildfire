#!/usr/bin/env python3
"""Flat-field and dark-frame correction for the three channels.

The K-line measurement is a ratio between channels, so any difference in how
much light each channel delivers for the same scene is indistinguishable from
real spectral structure. A 10 nm filter dilutes the potassium line to a modest
percentage excess over continuum, so a channel gain error of the same order
is not a nuisance - it is indistinguishable from the thing being measured.

How large is it here? Measured on the IMX296 plate over an aligned overlap
region - the only valid way, since the cameras point at slightly different
scene content - the three channels agree to 2.3%. Earlier figures of 35% and
150% from this project were measured by comparing whole-frame means across
cameras framing different parts of a scene, and were mostly scene, not gain.
The honest position is that the true per-channel gain is unknown until it is
measured against a uniform target, which is what this tool is for.

This applies the standard correction:

    corrected = (raw - dark) * gain,    gain(x, y) = target / (flat - dark)

which removes three things at once: the per-channel scalar gain difference,
the spatial vignetting roll-off, and the sensor's dark offset.

ORDER MATTERS: run this BEFORE register_triplets.py. The gain map is tied to
the sensor's pixel grid, so it must be applied while the frame is still in
sensor coordinates. Registration warps the image and the map would no longer
describe the pixels underneath it.

    capture -> flat_field.py apply -> register_triplets.py -> K-line index

Dark frames matter more than they look. At the exposures needed through 10 nm
filters the sensor's offset is a real fraction of the signal, and an offset
biases a ratio directly - it does not cancel.

Usage:
    # once per optical configuration (and again after the filters go on)
    tools/flat_field.py calibrate --flat ./flat --dark ./dark -o correction.npz

    # then, on every science capture
    tools/flat_field.py apply ./raw --correction correction.npz -o ./corrected

Requires: numpy, opencv-python (or opencv-python-headless).
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - dependency hint
    sys.exit("flat_field.py needs numpy and opencv.\n"
             "    pip install numpy opencv-python-headless")

FRAME_RE = re.compile(
    r"^(?P<stem>\d{8}_\d{6}_\d{3})_cam(?P<port>\d+)_(?P<wl>\d+)nm\.(?P<ext>jpe?g|png|tiff?)$",
    re.IGNORECASE,
)

# The flat is a picture of a wall, so it carries the wall's texture as well as
# the instrument's response. Blurring hard removes the texture and keeps the
# vignetting, which varies only slowly across the frame. Sigma is a fraction
# of frame width so it scales with resolution.
SMOOTH_FRACTION = 0.06

# A gain outside this range means the flat was near zero somewhere - a dead
# corner, or a flat that was itself underexposed. Clamping stops one dark
# pixel turning into a screaming bright one.
GAIN_LIMITS = (0.1, 10.0)

# Output is 16-bit PNG in 8.8 fixed point: stored = corrected * 256. Keeps the
# precision that gain-boosted dark corners would lose in 8-bit, stays lossless
# and viewable, and the K-line ratio is scale-invariant anyway.
OUTPUT_SCALE = 256.0


def scan(directory: Path) -> dict[int, list[Path]]:
    """Group frames in a directory by camera port."""
    out: dict[int, list[Path]] = defaultdict(list)
    for p in sorted(directory.iterdir()):
        if not p.is_file():
            continue
        m = FRAME_RE.match(p.name)
        if m:
            out[int(m["port"])].append(p)
    return dict(out)


def _read(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError(f"could not read {path}")
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img = img.astype(np.float32)
    # Normalise 16-bit input back to the nominal 0-255 scale so calibration
    # and science frames are always in the same units.
    if img.max() > 255.0:
        img /= OUTPUT_SCALE
    return img


def stack_mean(paths: list[Path]) -> np.ndarray:
    """Average several frames. Averaging N frames cuts read noise by sqrt(N),
    which matters because every science frame inherits this map's noise."""
    acc = None
    for p in paths:
        f = _read(p)
        acc = f if acc is None else acc + f
    return acc / len(paths)


def calibrate(flat_dir: Path, dark_dir: Path | None, out_path: Path) -> int:
    flats = scan(flat_dir)
    if not flats:
        sys.exit(f"no capture frames found in {flat_dir}")
    darks = scan(dark_dir) if dark_dir else {}

    ports = sorted(flats)
    print(f"flat frames: " + ", ".join(f"cam{p}={len(flats[p])}" for p in ports))
    if darks:
        print(f"dark frames: " + ", ".join(f"cam{p}={len(darks.get(p, []))}" for p in ports))
    else:
        print("dark frames: NONE — sensor offset will not be removed.")
        print("  Shoot a dark (lens caps on, same exposure) and re-run; at the")
        print("  exposures needed through 10 nm filters the offset biases the ratio.")
    print()

    dark_maps, flat_maps = {}, {}
    for port in ports:
        d = stack_mean(darks[port]) if darks.get(port) else None
        f = stack_mean(flats[port])
        if d is not None:
            f = f - d
        dark_maps[port] = d
        flat_maps[port] = f

    # Report what we are about to correct, before correcting it.
    print("BEFORE correction — the flat should be identical in all channels,")
    print("since with no filters fitted every camera sees the same light:")
    for port in ports:
        f = flat_maps[port]
        print(f"  cam{port}: mean={f.mean():7.2f}  "
              f"centre/corner={_centre_corner_ratio(f):5.2f}")
    means = [flat_maps[p].mean() for p in ports]
    print(f"  channel spread: {(max(means)/max(min(means), 1e-6) - 1) * 100:.1f}% "
          f"between brightest and dimmest\n")

    # One target for every channel: this is what equalises them.
    target = float(np.mean([flat_maps[p].mean() for p in ports]))

    gains, clipped_any = {}, False
    for port in ports:
        smooth = cv2.GaussianBlur(
            flat_maps[port], (0, 0),
            sigmaX=SMOOTH_FRACTION * flat_maps[port].shape[1])
        with np.errstate(divide="ignore", invalid="ignore"):
            g = target / np.maximum(smooth, 1e-3)
        n_clipped = int(((g < GAIN_LIMITS[0]) | (g > GAIN_LIMITS[1])).sum())
        if n_clipped:
            clipped_any = True
            print(f"  cam{port}: {n_clipped} px ({n_clipped/g.size*100:.2f}%) "
                  f"had gain outside {GAIN_LIMITS} and were clamped")
        gains[port] = np.clip(g, *GAIN_LIMITS).astype(np.float32)

    if clipped_any:
        print("  Clamping usually means the flat was underexposed or unevenly lit.\n")

    print("AFTER correction — applying the map back to the flat it came from.")
    print("The channels should now agree; residual spread is the correction's error:")
    corrected = []
    for port in ports:
        c = flat_maps[port] * gains[port]
        corrected.append(c)
        print(f"  cam{port}: mean={c.mean():7.2f}  "
              f"centre/corner={_centre_corner_ratio(c):5.2f}")
    cmeans = [c.mean() for c in corrected]
    residual = (max(cmeans) / max(min(cmeans), 1e-6) - 1) * 100
    print(f"  channel spread: {residual:.2f}%")
    verdict = ("good" if residual < 2 else
               "usable" if residual < 5 else "POOR — re-shoot the flat")
    print(f"  -> {verdict}\n")

    np.savez_compressed(
        out_path,
        ports=np.array(ports),
        target=np.float32(target),
        **{f"gain_{p}": gains[p] for p in ports},
        **{f"dark_{p}": (dark_maps[p] if dark_maps[p] is not None
                         else np.zeros_like(gains[p])) for p in ports},
        has_dark=np.array([dark_maps[p] is not None for p in ports]),
    )
    print(f"wrote {out_path}")
    return 0


def _centre_corner_ratio(img: np.ndarray) -> float:
    """How much brighter the centre is than the corners: the vignetting depth."""
    h, w = img.shape
    centre = img[h//2 - h//8:h//2 + h//8, w//2 - w//8:w//2 + w//8].mean()
    corner = np.mean([img[:h//8, :w//8].mean(), img[:h//8, -w//8:].mean(),
                      img[-h//8:, :w//8].mean(), img[-h//8:, -w//8:].mean()])
    return float(centre / max(corner, 1e-6))


def _optical_centre(img: np.ndarray) -> tuple[float, float]:
    """Locate the optical axis: the brightest point of the heavily blurred frame.

    It is NOT the frame centre. These are M12 lenses in 3D-printed holders and
    the axis lands 58-105 px off centre on this payload's three cameras. That
    is harmless - the gain map is per-pixel and corrects whatever shape the
    response has - but a symmetry test that assumes a centred axis reads the
    offset as scene contamination and rejects a perfectly good flat.
    """
    sm = cv2.GaussianBlur(img, (0, 0), 0.12 * img.shape[1])
    cy, cx = np.unravel_index(int(np.argmax(sm)), sm.shape)
    return float(cx), float(cy)


def _radial_structure(img: np.ndarray) -> tuple[float, float]:
    """Percentage of the frame's shape that is NOT radially symmetric, and how
    far the optical axis sits from the frame centre.

    Real vignetting falls off smoothly from the optical axis, so it is nearly
    radially symmetric about that axis. Scene content - cloud, a lit wall, a
    branch in a corner - is not symmetric about anything. Comparing the
    smoothed frame against its own radial average separates the two without
    needing to know anything about the optics.
    """
    h, w = img.shape
    cx, cy = _optical_centre(img)
    sm = cv2.GaussianBlur(img, (0, 0), 0.05 * w)
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    nbins = 64
    idx = np.clip((r / r.max() * (nbins - 1)).astype(int), 0, nbins - 1)
    prof = np.bincount(idx.ravel(), sm.ravel(), nbins) / np.maximum(
        np.bincount(idx.ravel(), None, nbins), 1)
    resid = float(np.abs(sm - prof[idx]).mean() / max(sm.mean(), 1e-6) * 100)
    return resid, float(np.hypot(cx - w / 2.0, cy - h / 2.0))


def check(flat_dir: Path) -> int:
    """Judge whether a candidate flat is usable, before it corrupts anything."""
    frames = scan(flat_dir)
    if not frames:
        sys.exit(f"no capture frames found in {flat_dir}")

    print("A usable flat is featureless and unclipped. Vignetting is fine and")
    print("expected; scene structure is not, because it gets baked into every")
    print("corrected frame as a false correction.")
    print()
    print("DO NOT rest a diffuser against the front element. Measured on this")
    print("payload, paper touching the lens reported 6-37% MORE vignetting than")
    print("the same lens sees on a distant scene: a contact diffuser feeds light")
    print("in at wide angles and exaggerates off-axis falloff. It over-corrects,")
    print("and since it over-corrects by a different amount per camera it makes")
    print("the channel-to-channel ratio worse - the one thing that matters.")
    print("Use a distant uniform source: even overcast, or a lit white panel")
    print("far enough away to be out of the near field. Spectralon and")
    print("Fluorilon are made for this; Teflon sheet is a cheap substitute")
    print("and works well.")
    print()
    print(f"{'':<7}{'mean':>7}{'p99':>6}{'clipped':>9}"
          f"{'centre/corner':>15}{'structure':>11}{'axis off':>10}")

    verdicts = []
    for port in sorted(frames):
        img = stack_mean(frames[port])
        clip = float((img >= 254).mean() * 100)
        p99 = float(np.percentile(img, 99))
        struct, axis_off = _radial_structure(img)
        print(f"  cam{port}{img.mean():8.1f}{p99:6.0f}{clip:8.2f}%"
              f"{_centre_corner_ratio(img):15.2f}{struct:10.1f}%{axis_off:9.0f}px")
        verdicts.append((port, clip, p99, struct))

    print()
    bad = False
    for port, clip, p99, struct in verdicts:
        if clip > 0.1 or p99 >= 250:
            print(f"  cam{port}: CLIPPED ({clip:.2f}% at 255). A saturated pixel cannot")
            print(f"          report how much light it received, so the gain derived")
            print(f"          from it is wrong. Shorten the exposure.")
            bad = True
        if struct > 8.0:
            print(f"  cam{port}: NOT FEATURELESS ({struct:.1f}% of the frame's shape is")
            print(f"          not radially symmetric). Something in the scene is being")
            print(f"          mistaken for lens vignetting - cloud, a lit wall, an")
            print(f"          object at the edge. Use a diffuser or a uniform source.")
            bad = True

    print("  VERDICT: " + ("NOT USABLE — see above" if bad else
                           "usable — go ahead and calibrate"))
    return 1 if bad else 0


def apply(image_dir: Path, correction_path: Path, out_dir: Path) -> int:
    data = np.load(correction_path)
    ports = [int(p) for p in data["ports"]]
    gains = {p: data[f"gain_{p}"] for p in ports}
    darks = {p: data[f"dark_{p}"] for p in ports}

    out_dir.mkdir(parents=True, exist_ok=True)
    frames = scan(image_dir)
    if not frames:
        sys.exit(f"no capture frames found in {image_dir}")

    n_out = n_clip = 0
    for port, paths in sorted(frames.items()):
        if port not in gains:
            print(f"  cam{port}: no correction for this port — SKIPPED")
            continue
        for p in paths:
            raw = _read(p)
            if raw.shape != gains[port].shape:
                print(f"  {p.name}: {raw.shape[1]}x{raw.shape[0]} does not match "
                      f"the correction map — SKIPPED")
                continue
            corrected = (raw - darks[port]) * gains[port]
            over = int((corrected > 255.0).sum())
            n_clip += over
            scaled = np.clip(corrected * OUTPUT_SCALE, 0, 65535).astype(np.uint16)
            out = out_dir / (p.stem + ".png")
            cv2.imwrite(str(out), scaled)
            n_out += 1

    print(f"\nwrote {n_out} corrected frames to {out_dir}")
    print(f"16-bit PNG, 8.8 fixed point — divide by {OUTPUT_SCALE:.0f} for the 0-255 scale.")
    if n_clip:
        print(f"NOTE: {n_clip} pixels exceeded 255 after correction and were clipped.")
        print("  Expected where the raw frame was already saturated; if it is")
        print("  widespread, the science frames are over-exposed.")
    print("\nNext: tools/register_triplets.py on this directory.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Flat-field and dark-frame correction for the three channels.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[-1])
    sub = ap.add_subparsers(dest="mode", required=True)

    c = sub.add_parser("calibrate", help="build a correction from flat/dark captures")
    c.add_argument("--flat", type=Path, required=True,
                   help="directory of flat-field frames (evenly lit blank wall)")
    c.add_argument("--dark", type=Path, default=None,
                   help="directory of dark frames (lens caps on, same exposure)")
    c.add_argument("-o", "--output", type=Path, default=Path("correction.npz"))

    k = sub.add_parser("check", help="judge whether a candidate flat is usable")
    k.add_argument("flat_dir", type=Path)

    a = sub.add_parser("apply", help="apply a correction to science captures")
    a.add_argument("image_dir", type=Path)
    a.add_argument("--correction", type=Path, required=True)
    a.add_argument("-o", "--output", type=Path, required=True)

    args = ap.parse_args()
    if args.mode == "check":
        if not args.flat_dir.is_dir():
            sys.exit(f"not a directory: {args.flat_dir}")
        return check(args.flat_dir)
    if args.mode == "calibrate":
        if not args.flat.is_dir():
            sys.exit(f"not a directory: {args.flat}")
        if args.dark and not args.dark.is_dir():
            sys.exit(f"not a directory: {args.dark}")
        return calibrate(args.flat, args.dark, args.output)
    return apply(args.image_dir, args.correction, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
