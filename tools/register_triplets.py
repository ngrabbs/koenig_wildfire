#!/usr/bin/env python3
"""Co-register the three channels of each capture triplet.

The three cameras view the same scene through different narrowband filters,
so the frames overlap 95-99%. What we need is not a panorama - it is a
per-pixel alignment, so that pixel (x, y) is the same patch of ground in all
three channels and the K-line ratio can be evaluated per pixel.

Flight test TL-002 showed rotation between channels is ~0.1 deg and scale
~1.005, both negligible, while translation swings by hundreds of pixels
between capture events because the aircraft moves during the capture cycle.
So the model here is a 2-DOF translation estimated *per triplet* - a fixed
calibration cannot work while motion dominates. See
docs/channel_registration.md for the measurements behind that choice.

Usage:
    tools/register_triplets.py IMAGE_DIR [-o OUTPUT_DIR] [options]

    tools/register_triplets.py ~/payload_images -o ./aligned
    tools/register_triplets.py ./flight_data -o ./aligned --composite
    tools/register_triplets.py ./flight_data --report-only

Requires: numpy, opencv-python (or opencv-python-headless).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - dependency hint
    sys.exit(
        "register_triplets.py needs numpy and opencv.\n"
        "    pip install numpy opencv-python-headless"
    )


# Capture filenames look like: 20260829_120648_759_cam0_762nm.jpg
# The stem before _cam is the capture-event id shared by all three channels.
FRAME_RE = re.compile(
    r"^(?P<stem>\d{8}_\d{6}_\d{3})_cam(?P<port>\d+)_(?P<wl>\d+)nm\.(?P<ext>jpe?g|png|tiff?)$",
    re.IGNORECASE,
)

# Phase correlation is estimated on a downscaled copy: it is much faster, and
# the low-frequency content that survives downscaling is exactly what carries
# the global shift. Sub-pixel accuracy on the small image scales back up fine
# for our purposes - we are correcting hundreds of pixels, not fractions.
WORK_WIDTH = 1024

# Reliability is judged by normalised cross-correlation between the reference
# and the shifted frame, measured over the region valid in both.
#
# NOT by cv2.phaseCorrelate's own `response` value: on TL-002 data that number
# is uncorrelated with whether the fit is any good. Event 20260829_120648_759
# scores response=0.104 while genuinely improving NCC from 0.331 to 0.694, and
# 20260829_121016_613 scores response=0.009 on the best-exposed frames of the
# flight. Applying the shift and measuring whether alignment actually improved
# is slower, but it is the thing we care about rather than a proxy for it.
MIN_NCC = 0.5

# Mean Sobel gradient magnitude, measured on the downscaled frame *before*
# CLAHE. A blank frame will "align" to NCC ~1.0 against another blank frame
# while telling you nothing, so gate on there being some content at all.
# Measured on TL-002 frames: fully blown white sits at 0.0-0.5, featureless
# grey at ~2.9, frames with real structure at 4.0 and up.
MIN_TEXTURE = 1.0


@dataclass
class ChannelShift:
    port: int
    wavelength_nm: int
    dx: float
    dy: float
    ncc: float           # after alignment, over the valid overlap
    ncc_before: float    # at zero shift, same region - shows what the fit bought
    texture: float
    response: float      # cv2.phaseCorrelate's own score, diagnostic only
    reliable: bool


@dataclass
class TripletResult:
    stem: str
    reference_port: int
    shifts: list[ChannelShift]
    status: str          # "ok" | "low-confidence" | "incomplete"
    note: str = ""


def find_triplets(image_dir: Path) -> tuple[dict[str, dict[int, Path]], dict[str, dict[int, int]]]:
    """Group frames by capture-event stem. Returns (paths, wavelengths)."""
    groups: dict[str, dict[int, Path]] = defaultdict(dict)
    waves: dict[str, dict[int, int]] = defaultdict(dict)
    for p in sorted(image_dir.iterdir()):
        if not p.is_file():
            continue
        m = FRAME_RE.match(p.name)
        if not m:
            continue
        stem, port = m["stem"], int(m["port"])
        groups[stem][port] = p
        waves[stem][port] = int(m["wl"])
    return dict(groups), dict(waves)


def _prepare(img: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Grayscale, downscale, and flatten illumination for correlation.

    CLAHE matters here for two reasons: the channels are different
    wavelengths so their absolute brightness differs (TL-002 measured cam2
    running 2.5x hot), and many frames are partly clipped. Local contrast
    equalisation puts all three on comparable footing before correlating.

    Returns (prepared, scale, texture). Texture is measured before CLAHE,
    because CLAHE amplifies noise in a flat frame and would mask exactly the
    case we are trying to detect.
    """
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if img.dtype == np.uint16:
        # CLAHE handles 16-bit, but the texture threshold below was measured
        # on an 8-bit scale, so bring 16-bit down to the same units.
        img = cv2.convertScaleAbs(img, alpha=1.0 / 256.0)
    elif img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    scale = WORK_WIDTH / img.shape[1]
    if scale < 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        scale = 1.0
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    texture = float(np.sqrt(gx * gx + gy * gy).mean())
    img = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(img)
    return img.astype(np.float32), scale, texture


def _ncc(a: np.ndarray, b: np.ndarray, dx: float, dy: float) -> float:
    """Normalised cross-correlation after shifting `b` by (-dx, -dy).

    Measured only over the region valid in both frames. Including the black
    border that warpAffine introduces would penalise large shifts purely for
    being large, which inverts the ranking we want.
    """
    h, w = a.shape
    shifted = cv2.warpAffine(b, np.float32([[1, 0, -dx], [0, 1, -dy]]), (w, h))
    x0, x1 = int(max(0, np.ceil(-dx))), int(min(w, np.floor(w - max(0, dx))))
    y0, y1 = int(max(0, np.ceil(-dy))), int(min(h, np.floor(h - max(0, dy))))
    if x1 - x0 < 50 or y1 - y0 < 50:
        return 0.0
    A = a[y0:y1, x0:x1].astype(np.float64)
    B = shifted[y0:y1, x0:x1].astype(np.float64)
    A -= A.mean()
    B -= B.mean()
    denom = np.sqrt((A * A).sum() * (B * B).sum())
    return float((A * B).sum() / denom) if denom > 0 else 0.0


def estimate_shift(ref: np.ndarray, mov: np.ndarray):
    """Translation that maps `mov` onto `ref`, via phase correlation.

    Returns (dx, dy, ncc, ncc_before, texture, response). The shift is in
    full-resolution pixels; texture is the weaker of the two frames. A Hanning
    window suppresses the edge discontinuity that would otherwise dominate the
    correlation peak.
    """
    a, scale, tex_a = _prepare(ref)
    b, _, tex_b = _prepare(mov)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    # phaseCorrelate returns the displacement of b relative to a, so b is
    # brought back onto a by shifting it the other way. Verified against a
    # synthetic known-shift pair.
    (dx, dy), response = cv2.phaseCorrelate(a, b, window)
    before = _ncc(a, b, 0.0, 0.0)
    after = _ncc(a, b, dx, dy)
    return dx / scale, dy / scale, after, before, min(tex_a, tex_b), float(response)


def translate(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Shift `img` by (-dx, -dy), i.e. move it back onto the reference."""
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    return cv2.warpAffine(
        img, M, (img.shape[1], img.shape[0]),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )


def common_crop(shape: tuple[int, int], shifts: list[tuple[float, float]]) -> tuple[int, int, int, int]:
    """Rectangle valid in every channel after shifting. Returns (x0, y0, x1, y1)."""
    h, w = shape[:2]
    left = int(np.ceil(max(0.0, *(max(0.0, -dx) for dx, _ in shifts))))
    right = int(np.floor(w - max(0.0, *(max(0.0, dx) for dx, _ in shifts))))
    top = int(np.ceil(max(0.0, *(max(0.0, -dy) for _, dy in shifts))))
    bottom = int(np.floor(h - max(0.0, *(max(0.0, dy) for _, dy in shifts))))
    return left, top, max(left + 1, right), max(top + 1, bottom)


def process_triplet(
    stem: str,
    frames: dict[int, Path],
    waves: dict[int, int],
    out_dir: Optional[Path],
    reference: int,
    write_composite: bool,
    crop: bool,
) -> TripletResult:
    ports = sorted(frames)
    if reference not in frames:
        return TripletResult(stem, reference, [], "incomplete",
                             f"reference cam{reference} missing (have {ports})")
    if len(frames) < 3:
        missing = [p for p in (0, 1, 2) if p not in frames]
        return TripletResult(stem, reference, [], "incomplete",
                             f"missing cam{','.join(map(str, missing))}")

    # UNCHANGED, not COLOR: flat_field.py emits 16-bit mono, and forcing
    # 8-bit here would discard the precision that correction just recovered
    # in the gain-boosted corners.
    images = {p: cv2.imread(str(frames[p]), cv2.IMREAD_UNCHANGED) for p in ports}
    if any(im is None for im in images.values()):
        return TripletResult(stem, reference, [], "incomplete", "unreadable frame")

    ref_img = images[reference]
    shifts: list[ChannelShift] = []
    offsets: dict[int, tuple[float, float]] = {reference: (0.0, 0.0)}

    for port in ports:
        if port == reference:
            shifts.append(ChannelShift(port, waves[port], 0.0, 0.0, 1.0, 1.0, 99.0, 1.0, True))
            continue
        dx, dy, ncc, ncc0, tex, resp = estimate_shift(ref_img, images[port])
        offsets[port] = (dx, dy)
        shifts.append(ChannelShift(
            port, waves[port], round(dx, 2), round(dy, 2),
            round(ncc, 3), round(ncc0, 3), round(tex, 2), round(resp, 4),
            ncc >= MIN_NCC and tex >= MIN_TEXTURE,
        ))

    status = "ok" if all(s.reliable for s in shifts) else "low-confidence"
    reasons = []
    if any(s.ncc < MIN_NCC for s in shifts):
        reasons.append("channels do not correlate after alignment")
    if any(s.texture < MIN_TEXTURE for s in shifts):
        reasons.append("frame too blown out or too flat to carry structure")
    note = "; ".join(reasons)

    if out_dir is not None:
        aligned = {p: (images[p] if p == reference else translate(images[p], *offsets[p]))
                   for p in ports}
        if crop:
            x0, y0, x1, y1 = common_crop(ref_img.shape, list(offsets.values()))
            aligned = {p: im[y0:y1, x0:x1] for p, im in aligned.items()}
        for p, im in aligned.items():
            cv2.imwrite(str(out_dir / f"{stem}_cam{p}_{waves[p]}nm_aligned.png"), im)
        if write_composite:
            # False-colour QA image: one channel per RGB plane. If registration
            # worked, edges are grey; coloured fringes mean residual misalignment.
            # Always 8-bit — this is for eyeballing, not for measurement.
            planes = []
            for p in sorted(aligned):
                im = aligned[p]
                if im.ndim == 3:
                    im = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
                if im.dtype != np.uint8:
                    im = cv2.convertScaleAbs(im, alpha=255.0 / max(float(im.max()), 1.0))
                planes.append(im)
            cv2.imwrite(str(out_dir / f"{stem}_composite.png"),
                        cv2.merge([planes[2], planes[1], planes[0]]))

    return TripletResult(stem, reference, shifts, status, note)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Co-register the three channels of each capture triplet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[-1],
    )
    ap.add_argument("image_dir", type=Path, help="directory of capture frames")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="write aligned frames here (default: report only)")
    ap.add_argument("--reference", type=int, default=0, metavar="PORT",
                    help="channel everything is aligned onto (default: 0)")
    ap.add_argument("--composite", action="store_true",
                    help="also write a false-colour RGB composite per triplet for visual QA")
    ap.add_argument("--no-crop", action="store_true",
                    help="keep full frame instead of cropping to the region valid in all channels")
    ap.add_argument("--report-only", action="store_true",
                    help="estimate and report shifts without writing images")
    ap.add_argument("--report", type=Path, default=None,
                    help="write the shift report here (.json or .csv; default: stdout only)")
    args = ap.parse_args()

    if not args.image_dir.is_dir():
        sys.exit(f"not a directory: {args.image_dir}")

    out_dir = None if args.report_only else args.output
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    groups, waves = find_triplets(args.image_dir)
    if not groups:
        sys.exit(f"no frames matching the capture naming convention in {args.image_dir}\n"
                 f"    expected e.g. 20260829_120648_759_cam0_762nm.jpg")

    print(f"{len(groups)} capture events in {args.image_dir}")
    if out_dir:
        print(f"writing aligned frames to {out_dir}")
    print()

    results: list[TripletResult] = []
    for stem in sorted(groups):
        r = process_triplet(stem, groups[stem], waves[stem], out_dir,
                            args.reference, args.composite, not args.no_crop)
        results.append(r)
        if r.status == "incomplete":
            print(f"  {stem}  SKIPPED - {r.note}")
        else:
            moved = [s for s in r.shifts if s.port != r.reference_port]
            desc = "  ".join(
                f"cam{s.port}: dx={s.dx:+8.1f} dy={s.dy:+8.1f} "
                f"(ncc {s.ncc_before:.2f}->{s.ncc:.2f} t={s.texture:.1f})"
                for s in moved
            )
            flag = "" if r.status == "ok" else "   [LOW CONFIDENCE]"
            print(f"  {stem}  {desc}{flag}")

    ok = sum(r.status == "ok" for r in results)
    low = sum(r.status == "low-confidence" for r in results)
    bad = sum(r.status == "incomplete" for r in results)
    print(f"\n{ok} registered, {low} low-confidence, {bad} incomplete, {len(results)} total")

    if low:
        print("  Low-confidence triplets are usually an exposure problem: a clipped")
        print("  or featureless frame has no gradient left to correlate against, and")
        print("  a featureless frame will happily report a confident zero shift.")
        print("  Their aligned output is still written - treat the shift as unverified.")
    if bad:
        print("  Incomplete triplets are a capture-side bug (TL-002 anomaly A1):")
        print("  the capture routine should guarantee three frames or mark the event.")

    if args.report:
        payload = [asdict(r) for r in results]
        if args.report.suffix.lower() == ".csv":
            with args.report.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["stem", "status", "port", "wavelength_nm", "dx", "dy",
                            "ncc", "ncc_before", "texture", "response", "reliable"])
                for r in results:
                    for s in r.shifts:
                        w.writerow([r.stem, r.status, s.port, s.wavelength_nm,
                                    s.dx, s.dy, s.ncc, s.ncc_before, s.texture,
                                    s.response, s.reliable])
        else:
            args.report.write_text(json.dumps(payload, indent=2))
        print(f"\nreport written to {args.report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
