---
title: "Koenig Wildfire — Three-Channel Registration"
subtitle: "Why the channels don't line up, what we measured, and what we're doing about it"
date: "2026-08-29 — findings from flight test TL-002"
---

# The question

After the 2026-08-29 drone session, the request was to "write some code that
lines up the overlap of each picture into one large picture."

This document records what the flight data actually shows, what the problem
turned out to be, and the plan. It exists so that the next person to pick this
up does not have to re-derive it.

**Source data:** 122 frames / 41 capture events from
`ECE-4512 Capstone I/payload/testing_aug_29/testing_data/`, logged as TL-002.

# First: this is co-registration, not stitching

"Splice into one large picture" describes **panorama stitching** — several
cameras covering different parts of a scene, mosaicked into a wider image.
That is not what this payload does.

All three cameras point at the **same** scene through different filters.
Measured frame overlap across the flight set is **95–99%** in the median case.
The cameras are not covering different ground; they are covering the same
ground in different colours.

What we actually need is **co-registration**: warp all three frames onto a
common pixel grid so that pixel `(x, y)` is the same patch of ground in every
channel, then stack them into one three-channel image and evaluate the K-line
ratio per pixel.

> The output is **one image the size of one camera**, with three spectral
> channels — not a larger image. Getting this distinction wrong sends you
> after panorama libraries that solve a problem we do not have.

# What we measured

Feature matching (ORB + RANSAC, CLAHE-normalised) across all 40 complete
triplets, estimating the transform from cam1 and cam2 onto cam0.

## The cameras are mechanically excellent

| | cam1 → cam0 | cam2 → cam0 |
|---|---|---|
| Rotation (median) | −0.03° | −0.11° |
| Scale (median) | 1.003 | 1.007 |

Rotation is essentially zero and scale is essentially unity. The plate holds
the three cameras coplanar and co-aligned, and the three lenses are at
matching focal lengths.

**Consequence: we do not need homographies, lens distortion models, or any
projective machinery.** A translation-only model is very nearly sufficient.

## But translation is not constant

| | cam1 → cam0 | cam2 → cam0 |
|---|---|---|
| dx median / IQR | +17 px / **212 px** | +70 px / **487 px** |
| dy median / IQR | +28 px / **399 px** | +103 px / **545 px** |

If the payload were rigid and the scene static, this offset would be a fixed
number you could measure once and apply forever. It is not — it swings by
hundreds of pixels between one capture event and the next.

## The offset is motion, not geometry

The three cameras sit roughly 40 mm apart on the plate. Geometric parallax
from that baseline is small:

| Lens | Parallax at 5 m | at 12 m |
|---|---|---|
| 4 mm | 21 px | 9 px |
| 6 mm | 31 px | 13 px |
| 8 mm | 41 px | 17 px |

**Tens of pixels of parallax against hundreds of pixels of observed offset.**
The offset is dominated by something else, and EXIF says what:

```
cam0   13:06:49   .../pca@70/i2c@2/imx477
cam1   13:06:50   .../pca@70/i2c@1/imx477
cam2   13:06:50   .../pca@70/i2c@0/imx477
```

**Every triplet spans 1–2 seconds** from first channel to last (24 of 40
triplets at ≥1 s, 16 at ≥2 s; EXIF resolution is 1 s, so these are lower
bounds). The architecture document budgeted **≤300 ms** for the whole
three-channel cycle. We are 3–6× over.

A drone drifting even slowly moves a long way in a second and a half. **The
variable offset between channels is the aircraft moving between exposures.**

# Root cause: the capture cycle is too slow

Benchmarked on the rig, three-channel cycle, warm (2026-08-29):

| Resolution | Current pattern | Grab phase only |
|---|---|---|
| 4056 × 3040 | **1.180 s** | 0.885 s |
| 2028 × 1520 | 0.515 s | **0.452 s** |
| 1332 × 990 | 0.443 s | 0.413 s |

Per-channel breakdown at full resolution:

| Step | Cost |
|---|---|
| `start()` | 0.014–0.030 s |
| `capture_file()` (grab + JPEG encode) | 0.356–0.361 s |
| `stop()` | 0.019–0.022 s |
| `capture_array()` (grab only, no encode) | 0.262 s |

Two things this rules out and one it confirms:

- **Not** the mux switching. `start()` and `stop()` cost ~20 ms each. The
  kernel video-mux driver is fast.
- **Not** camera open/configure. That is 0.159 s once, at daemon start.
- **It is** the per-channel frame grab (~262 ms) plus the synchronous JPEG
  encode (~100 ms) inside `capture_file()`, three times over.

There is a floor of roughly **135 ms per channel** that does not go away with
resolution — that is pipeline fill after `start()`. We cannot avoid the
stop/start cycle because the Arducam v2.2 is a CSI *switch*: only one camera
can be streaming at a time.

# Exposure: most of the flight data is clipped

Separate finding from the same data set, and it matters more than
registration does.

| Quality | Frames | Share |
|---|---|---|
| Usable (<1% clipped) | 23 | 19% |
| Mild (1–10%) | 14 | 11% |
| Bad (10–50%) | 37 | 30% |
| Severe (>50%) | 42 | 34% |
| Near-black | 6 | 5% |

**64% of the flight frames are badly or severely clipped.** Exposure was
being chased down in the field over the session — 50 ms → 4.97 ms → 2.49 ms
→ 0.99 ms — and the last group is the best of the day:

| Window | Exposure | Median clipped |
|---|---|---|
| 12:00:58 – 12:02:08 | 4.974 ms | 76–93% |
| 12:02:18 – 12:03:18 | 4.974 ms | 0% ✓ |
| 12:06:23 – 12:07:43 | 2.487 ms | 28–88% |
| 12:09:46 – 12:10:16 | 0.994 ms | **3–14%** ✓ best |

This also explains why registration succeeded on only 25 of 40 triplets:
a clipped frame has no gradient left to match against.

## Two consequences

**For the burn test, expose for the flame, not the landscape.** The flame is
the brightest thing in the frame and it is the thing being measured. A
saturated flame makes the ratio meaningless no matter how good the alignment
is. Go shorter than looks correct for the background, and bracket.

**The filters will change this substantially.** A 10 nm FWHM bandpass passes
on the order of 1–2% of the broadband light the cameras see now — call it six
stops. The 1 ms exposure that works today becomes **50–100 ms** once filters
are fitted. That loops straight back into registration: at 50–100 ms on a
moving aircraft you get motion blur *within* each frame on top of
displacement *between* frames.

# Filters — ordered 2026-08-29

Thorlabs hard-coated bandpass, Ø25 mm, **10 nm FWHM** each:

| Part | CWL | Role |
|---|---|---|
| `FBH750-10` | 750 nm | Continuum reference, below the line |
| `FBH770-10` | 770 nm | **On-line** — the K I doublet (766.5 / 769.9 nm) |
| `FBH780-10` | 780 nm | Continuum reference, above the line |

This is a **bracketing** design and supersedes the 762 / 766 / 770 scheme
described elsewhere in the repo. Two changes follow from it:

- The continuum under the line is **interpolated between 750 and 780** rather
  than assumed equal to a single off-line reference. Better estimate, and it
  keeps both reference channels clear of the O₂ A-band (~759–771 nm) that the
  on-line channel unavoidably sits inside.
- **The ratio math in `architecture.md` is now out of date.** It still
  documents `(S766 + S770) / (2 · S762)`.

> **Pending rename.** `CHANNELS` in `pi/daemon/camera.py` still maps ports to
> 762 / 766 / 770, and those numbers appear in every capture filename. The
> rename is deliberately deferred until the filters physically arrive
> (TL-002 Action 2) so that the label change and the optical change land
> together and no data set is ambiguous about which it is.

# What we are going to do, and why

In priority order.

### 1. Cut the capture cycle time

**Why first:** it attacks the root cause rather than compensating for it.
Every millisecond removed from the inter-channel window is displacement that
never has to be corrected, blur that never happens, and scene change that
never occurs. Registration gets easier as a side effect.

Two changes, both straightforward:

- **Drop capture resolution to 2028 × 1520.** More than halves the cycle
  (1.180 s → 0.515 s). This is the sensor's native 2×2 binned mode, so it
  keeps the **full field of view** — unlike 1332 × 990, which crops. It is
  still 3.1 MP.

  Binning is a **second win specific to this payload**: summing four photosites
  per output pixel improves signal-to-noise, and once the narrowband filters
  cut ~98% of the light, SNR is exactly what we will be short of. Lower
  resolution is not a compromise here — it is the better choice on both axes.

- **Move JPEG encoding off the inter-channel path.** Grab all three frames
  with `capture_array()` first, then encode. At 2028 × 1520 this takes the
  window from 0.515 s to 0.452 s; at full resolution 1.180 s → 0.885 s.

**Measured result, 2026-08-29.** Both changes are implemented and verified
on the rig, timed end-to-end through the daemon's `/capture` endpoint:

| Resolution | 3-channel cycle |
|---|---|
| 4056 × 3040 | 1.01 – 1.15 s |
| 2028 × 1520 | **0.414 – 0.419 s** |

**2.5× faster**, and better than the 0.45 s the component benchmarks
predicted — the encode split gains a little more in practice than in
isolation. Against the 1–2 s seen in flight, inter-channel displacement
should fall by roughly a factor of three to four.

This is within 1.4× of the architecture document's 300 ms budget. The
remaining cost is the ~135 ms/channel pipeline fill, which we are not
attacking yet (see the table below).

> **Deployment note.** `DEFAULT_RESOLUTION` only applies to a fresh
> install — an existing `~/.koenig/settings.json` keeps whatever it already
> stored. The rig at `192.168.1.46` has been switched to 2028 × 1520
> explicitly. Any other unit needs the same change, from the settings page
> or by deleting the stored resolution key.

### 2. Register per triplet, not once

**Why:** a fixed calibration cannot work while platform motion dominates the
offset. The transform has to be estimated from each triplet's own content.

**How:** `cv2.phaseCorrelate` on downscaled grayscale, estimating 2-DOF
translation per pair, then warp cam1 and cam2 onto cam0. Rotation and scale
are measured at ~0.1° and ~1.005, which is below what matters here, so the
full ORB/RANSAC/homography path is not justified. Roughly 50 lines, runs in
milliseconds, and degrades predictably.

Register on **background structure, not the fire**. During a burn the on-line
channel will show something the reference channels do not — that difference
*is* the signal, and an aligner allowed to match it away will erase it.

### 3. Operational: capture in a hover

Costs nothing, available immediately, and directly reduces the displacement
the aligner has to solve. Start a timer capture, hover over the target, land.
Do not fly a translating pass and expect clean triplets.

# What we are deliberately not doing

| Not doing | Why |
|---|---|
| Panorama stitching | The frames are 95–99% overlapped. There is no mosaic to build. |
| Homography / projective warp | Measured rotation ≈ 0.1°, scale ≈ 1.005. Translation-only is sufficient and far more robust on low-texture frames. |
| One-time factory calibration | Motion dominates geometry by an order of magnitude. A fixed offset would be wrong on every frame. |
| Enabling auto-exposure | Each camera would pick its own exposure for the same scene and the channel ratio would be meaningless. Exposure must stay fixed and shared. |
| Lens distortion correction | Not yet measured, and swamped by everything above. Revisit after the cycle-time and exposure work, if residual misalignment justifies it. |
| Chasing the 135 ms/channel floor | That is pipeline fill after `start()`, and the CSI switch forbids keeping more than one camera streaming. Not worth attacking until the rest is done. |

# Open questions

- **Does the aligner survive real spectral differences?** Everything measured
  here is broadband — all three channels saw the same light. Once filters are
  fitted the channels genuinely differ, and feature matching across bands is
  harder. 750 / 770 / 780 nm are within 4% of each other so scene structure
  should be nearly identical, but this needs testing rather than assuming.
- **How much does a hover actually help?** Untested. One session comparing
  hover captures against translating-pass captures would quantify it.
- **Is 0.45 s good enough?** Unknown until we fly it. If not, the remaining
  levers are a global-shutter sensor (IMX296, blocked on `imx296.dtsi`) or
  hardware-synchronised capture (Arducam Camarray, cost).
