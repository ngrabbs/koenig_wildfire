#!/usr/bin/env python3
"""Compute the potassium K-index (delta-77) from an aligned three-channel set.

Burning vegetation emits neutral potassium at 766.49 and 769.90 nm. The
770 nm channel sees that emission plus whatever continuum the scene is
already producing; the 750 and 780 nm channels see continuum only. Since
770 nm lies two thirds of the way from 750 to 780, the continuum underneath
the line is the linear interpolation between them:

    C770 = (1/3) * (S750 + 2 * S780)

and the index is the fractional excess of the on-line channel over it:

                 S770 - (1/3)(S750 + 2*S780)
    delta77  =  -----------------------------
                    (1/3)(S750 + 2*S780)

    delta77 > 0   the 770 channel carries light the continuum cannot explain
    delta77 = 0   no potassium emission
    delta77 < 0   the 770 channel is dimmer than the continuum predicts

Form and notation follow D. Koenig, who arrived at the same continuum
weights independently and uses the signed fractional excess rather than a
ratio centred on 1. Signed is the better choice: the sign carries meaning
and the threshold sits at zero.

INPUT MUST BE FLAT-FIELDED AND REGISTERED, in that order:

    capture -> flat_field.py apply -> register_triplets.py -> this

Neither is optional. The index is a ratio between channels, so any
per-channel gain difference is indistinguishable from potassium; and it is
evaluated per pixel, so channels that are not aligned compare different
patches of ground.

Usage:
    tools/k_index.py ALIGNED_DIR -o OUT_DIR
    tools/k_index.py ALIGNED_DIR -o OUT_DIR --roles 0=750,1=770,2=780
    tools/k_index.py ALIGNED_DIR --report-only

Requires: numpy, opencv-python. matplotlib for the spectral plot (optional).
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
except ImportError:  # pragma: no cover
    sys.exit("k_index.py needs numpy and opencv.\n"
             "    pip install -r tools/requirements.txt")

FRAME_RE = re.compile(
    r"^(?P<stem>\d{8}_\d{6}_\d{3})_cam(?P<port>\d+)_(?P<wl>\d+)nm"
    r"(?P<suffix>_aligned)?\.(?P<ext>jpe?g|png|tiff?)$", re.IGNORECASE)

# Port -> nominal wavelength. The default is the intended assignment for the
# ordered Thorlabs set. It is NOT read from the filename: captures are still
# named with the superseded 762/766/770 labels, and trusting those would
# silently compute the wrong thing. Override with --roles.
DEFAULT_ROLES = {0: 750, 1: 770, 2: 780}

# 16-bit input from flat_field.py is 8.8 fixed point.
INPUT_SCALE_16BIT = 256.0

# Pixels darker than this carry too little signal for a ratio to mean
# anything - the denominator approaches zero and the index explodes.
MIN_SIGNAL = 4.0


def scan(directory: Path) -> dict[str, dict[int, Path]]:
    groups: dict[str, dict[int, Path]] = defaultdict(dict)
    for p in sorted(directory.iterdir()):
        if not p.is_file():
            continue
        m = FRAME_RE.match(p.name)
        if m:
            groups[m["stem"]][int(m["port"])] = p
    return dict(groups)


def read_channel(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError(f"could not read {path}")
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img = img.astype(np.float32)
    if img.dtype == np.uint16 or img.max() > 255.0:
        img /= INPUT_SCALE_16BIT
    return img


def continuum(s_low: np.ndarray, s_high: np.ndarray,
              wl_low: float, wl_on: float, wl_high: float) -> np.ndarray:
    """Linear interpolation of the continuum at the on-line wavelength.

    Written from the wavelengths rather than hardcoding 1/3 and 2/3 so the
    close-range 760/770/780 set works without editing the maths. For
    750/770/780 the weights come out at exactly 1/3 and 2/3, matching the
    published form.
    """
    f = (wl_on - wl_low) / (wl_high - wl_low)
    return (1.0 - f) * s_low + f * s_high


def k_index(s_on: np.ndarray, s_cont: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """delta77 and a validity mask. Invalid where the continuum is too dark."""
    valid = s_cont >= MIN_SIGNAL
    out = np.zeros_like(s_on, dtype=np.float32)
    np.divide(s_on - s_cont, s_cont, out=out, where=valid)
    return out, valid


def colourise(delta: np.ndarray, valid: np.ndarray, limit: float) -> np.ndarray:
    """Blue below zero, red above, grey where invalid. Symmetric about zero
    so the eye reads sign, which is the whole point of a signed index."""
    n = np.clip(delta / max(limit, 1e-6), -1.0, 1.0)
    img = np.zeros((*delta.shape, 3), np.uint8)
    pos, neg = n > 0, n < 0
    img[..., 2] = np.where(pos, 40 + 215 * n, 40).astype(np.uint8)          # R
    img[..., 0] = np.where(neg, 40 + 215 * (-n), 40).astype(np.uint8)       # B
    img[..., 1] = 40
    img[~valid] = (90, 90, 90)
    return img


def spectral_plot(vals: dict[int, float], wls: dict[int, int],
                  delta: float, out: Path) -> bool:
    """Three measured points, the interpolated continuum, and the excess."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    order = sorted(wls, key=lambda p: wls[p])
    x = [wls[p] for p in order]
    y = [vals[p] for p in order]
    lo, on, hi = order[0], order[1], order[2]
    c = continuum(vals[lo], vals[hi], wls[lo], wls[on], wls[hi])

    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=140)
    ax.plot([wls[lo], wls[hi]], [vals[lo], vals[hi]], "--", color="#888",
            lw=1.4, label="interpolated continuum", zorder=1)
    ax.plot(x, y, "o", ms=9, color="#1f4e79", zorder=3, label="measured")
    ax.plot([wls[on]], [c], "o", ms=7, mfc="none", mec="#888", zorder=3)
    ax.annotate("", xy=(wls[on], vals[on]), xytext=(wls[on], c),
                arrowprops=dict(arrowstyle="<->", color="#b23a2e", lw=1.6), zorder=4)
    ax.text(wls[on] + 1.2, (vals[on] + c) / 2,
            f"$\\delta_{{77}}$ = {delta:+.3f}", color="#b23a2e", va="center")
    for p in order:
        ax.annotate(f"{wls[p]} nm", (wls[p], vals[p]), textcoords="offset points",
                    xytext=(0, -18), ha="center", fontsize=9, color="#444")
    ax.set_xlabel("wavelength (nm)")
    ax.set_ylabel("signal (corrected counts)")
    ax.set_title("Three-point sampling of the K-line region")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, fontsize=9, loc="best")
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compute the potassium K-index (delta-77) per pixel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[-1])
    ap.add_argument("image_dir", type=Path, help="aligned, flat-fielded triplets")
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument("--roles", default=None,
                    help="port=wavelength mapping, e.g. 0=750,1=770,2=780")
    ap.add_argument("--limit", type=float, default=0.5,
                    help="delta77 at full colour saturation in the map (default 0.5)")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    roles = dict(DEFAULT_ROLES)
    if args.roles:
        roles = {int(k): int(v) for k, v in
                 (part.split("=") for part in args.roles.split(","))}
    order = sorted(roles, key=lambda p: roles[p])
    if len(order) != 3:
        sys.exit("need exactly three channels")
    lo, on, hi = order
    print(f"channel roles: cam{lo}={roles[lo]}nm (continuum), "
          f"cam{on}={roles[on]}nm (on-line), cam{hi}={roles[hi]}nm (continuum)")
    f = (roles[on] - roles[lo]) / (roles[hi] - roles[lo])
    print(f"continuum weights: {1-f:.3f} x S{roles[lo]}  +  {f:.3f} x S{roles[hi]}\n")

    if not args.image_dir.is_dir():
        sys.exit(f"not a directory: {args.image_dir}")
    groups = scan(args.image_dir)
    if not groups:
        sys.exit(f"no capture frames found in {args.image_dir}")

    out_dir = None if args.report_only else args.output
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'capture event':<22}{'median':>9}{'p99':>9}{'>0.05':>8}{'valid':>8}")
    flat_like = 0
    for stem in sorted(groups):
        ch = groups[stem]
        if not all(p in ch for p in order):
            print(f"  {stem}  INCOMPLETE - skipped")
            continue
        s = {p: read_channel(ch[p]) for p in order}
        if len({v.shape for v in s.values()}) != 1:
            print(f"  {stem}  channel sizes differ - not registered? skipped")
            continue

        cont = continuum(s[lo], s[hi], roles[lo], roles[on], roles[hi])
        delta, valid = k_index(s[on], cont)
        v = delta[valid]
        if v.size == 0:
            print(f"  {stem}  no valid pixels (too dark)")
            continue
        med = float(np.median(v))
        p99 = float(np.percentile(v, 99))
        frac = float((v > 0.05).mean() * 100)
        print(f"  {stem:<20}{med:+9.3f}{p99:+9.3f}{frac:7.1f}%{valid.mean()*100:7.1f}%")
        if abs(med) < 0.02 and p99 < 0.10:
            flat_like += 1

        if out_dir:
            np.save(out_dir / f"{stem}_delta77.npy", delta.astype(np.float32))
            cv2.imwrite(str(out_dir / f"{stem}_delta77.png"),
                        colourise(delta, valid, args.limit))
            means = {p: float(s[p][valid].mean()) for p in order}
            cmean = continuum(means[lo], means[hi], roles[lo], roles[on], roles[hi])
            if not spectral_plot(means, roles, (means[on] - cmean) / max(cmean, 1e-6),
                                 out_dir / f"{stem}_spectrum.png"):
                print("      (matplotlib not installed - spectral plot skipped)")

    if out_dir:
        print(f"\nwrote to {out_dir}")
        print("  *_delta77.npy      float32 index per pixel, the analysis product")
        print("  *_delta77.png      red positive / blue negative, grey = too dark")
        print("  *_spectrum.png     the three points and the interpolated continuum")

    if flat_like:
        print(f"\nNOTE: {flat_like} event(s) came out flat near zero.")
        print("  Expected while no filters are fitted - every channel then sees")
        print("  identical broadband light, so the index is measuring noise. It")
        print("  is not evidence the instrument works, only that it is consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
