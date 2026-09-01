---
title: "Payload and Processing Review"
subtitle: "What the instrument does today, stage by stage, with worked examples"
date: "2026-08-30"
---

# Purpose of this document

This walks through the payload as it stands and the processing chain behind
it, one stage at a time, showing what goes into each stage and what comes
out. Every figure is a real capture from the current hardware, not an
illustration.

It also states plainly what the instrument cannot yet do. **The narrowband
filters have not arrived, so no potassium measurement has been made.**
Everything shown here is engineering data: three cameras looking at the same
scene through no filters at all. That is deliberate — it is the only
condition under which the instrument can be checked against a known answer,
and that opportunity disappears the moment the filters go on.

# Where the payload stands

The imaging hardware is complete and working. Three **Sony IMX296**
global-shutter monochrome cameras (InnoMaker CAM-IMX296RAW), 1456 × 1088,
on a 3D-printed plate, feeding a single Raspberry Pi 4 through an Arducam
Multi Camera Adapter v2.2. The whole payload — cameras, Pi, and battery —
is bolted into one rigid package that flies as a unit.

![The three-camera plate. Each camera is an IMX296 global-shutter monochrome sensor with an M12 lens; the filters bolt on in front of these.](img/rev_plate.jpg)

Two things about this sensor choice matter for the science:

**Global shutter.** Every pixel is exposed at the same instant, so a moving
aircraft produces no rolling-shutter skew. On the previous colour sensors it
did.

**True monochrome.** There is no Bayer colour filter array over the pixels.
Nothing interpolates, and nothing attenuates the near-infrared band we care
about. Every photon that reaches a pixel is measured by that pixel.

Getting these to run was not straightforward: the Raspberry Pi kernel ships
no device-tree support for an IMX296 behind a camera multiplexer, so that
had to be written. It now works, and all three cameras enumerate and capture
reliably.

# The processing chain

```mermaid
flowchart LR
    A["Capture<br/><i>3 raw frames</i>"] --> B["Flat-field<br/><i>instrument response</i>"]
    B --> C["Registration<br/><i>align the channels</i>"]
    C --> D["K-line index<br/><i>the measurement</i>"]
    style D stroke-dasharray: 5 5
```

Three stages are built and tested. The fourth is specified but not written,
because with no filters fitted it would return noise around 1.0 and there
would be no way to tell a correct implementation from a broken one.

The order is not arbitrary. Flat-fielding must come before registration:
the correction map describes the sensor's own pixels, so it has to be applied
while the frame is still in sensor coordinates. Registration moves the image,
and after that the map no longer lines up with what it describes.

# Stage 1 — Capture

**In:** an operator clicking Capture, or a timer.
**Out:** three JPEGs, one per camera, sharing a capture-event timestamp.

```
20260830_150216_444_cam0_762nm.jpg
20260830_150216_444_cam1_766nm.jpg
20260830_150216_444_cam2_770nm.jpg
```

![One capture event. The same scene through all three channels — currently identical light, because no filters are fitted.](img/rev_raw_triplet.jpg)

The multiplexer is a switch, not a true multiplexer: the Pi's camera
interface can only talk to one sensor at a time, so the three channels are
captured in sequence rather than simultaneously. The whole cycle takes
**about 2 seconds**.

That number is the single biggest limitation in the instrument, and it is
discussed under [What is not solved](#what-is-not-solved).

> **A caution about the filenames.** The `762nm` / `766nm` / `770nm` in each
> filename is the *intended* filter assignment, not a measurement. With no
> filters fitted, all three channels record identical broadband light. The
> labels will be updated to the real wavelengths when the filters are
> installed; until then, any analysis treating these as spectral channels
> will produce nonsense.

# Stage 2 — Flat-field correction

**In:** raw frames, plus a calibration built from a flat capture (uniform
featureless target) and a dark capture (lens caps on).
**Out:** frames with the instrument's own signature removed.

Each camera delivers a different amount of light to each part of its frame.
Lenses fall off toward the corners, and no two lenses or sensors are quite
identical. Since the potassium measurement is a *ratio between channels*, any
such difference is mathematically indistinguishable from real spectral
structure. The correction is:

$$\text{corrected} = (\text{raw} - \text{dark}) \times \text{gain},
\qquad \text{gain}(x,y) = \frac{\text{target}}{\text{flat} - \text{dark}}$$

![Correction maps derived for the three cameras. Dark is low gain, bright is high gain: each map is the inverse of that camera's own light falloff.](img/rev_gainmaps.jpg)

The tool is written and validated. Against a synthetic test set with known
errors deliberately injected — channel gains of 1.00 / 1.35 / 0.85, per-camera
dark offsets, and a vignetting profile — it recovered them exactly:

| | before correction | after correction |
|---|---|---|
| channel spread (flat) | 58.8% | **0.00%** |
| cam1 / cam0 per pixel | 1.3707 | **1.0000** |
| cam2 / cam0 per pixel | 0.8849 | **1.0000** |

with residual scatter equal to the injected noise — no systematic error left.

## Two honest findings from calibrating the real instrument

**The channels already agree to about 2%.** Measured on the same pixels of
the same scene, the three cameras read 109.8, 111.8 and 109.3 — a 2.3%
spread. They are near-identical parts with near-identical optics, so most of
the instrument signature cancels in the ratio before any correction is
applied. Flat-fielding here is a refinement, not a rescue, and any
calibration has to beat that 2% baseline to be worth applying.

**The flat we have is not good enough to apply.** The first attempt used
paper resting on the lenses as a diffuser. That turns out to be an invalid
flat: light entering at wide angles from a diffuser touching the front
element exaggerates the lens falloff, and the flat reported **6–37% more
vignetting than the same lens sees on a real distant scene**. Correcting with
it over-corrects, by a different amount on each camera, which made the
channel ratio worse rather than better.

The fix is a distant uniform source, and it is a ten-second capture once
one is available. Spectralon and Fluorilon are engineered
diffuse-reflectance standards intended for this; Teflon sheet is a cheap and
reasonable substitute, and is what D. Koenig's group uses. The correction has to be re-derived through the filters
anyway, since filter transmission varies unit to unit and will not cancel the
way matched lenses do.

# Stage 3 — Channel registration

**In:** three corrected frames from one capture event.
**Out:** three frames on a common pixel grid, cropped to the region visible
in all of them, plus a report of the shift applied.

The three cameras sit about 40 mm apart and are captured seconds apart, so
they do not see quite the same thing. Before the ratio can be evaluated, the
frames have to be aligned so that a given pixel is the same patch of ground
in all three.

![The same pixel box in two channels, from the drone at about 15–20 ft. Top: unaligned — the cameras are offset, so the diagonal path and the bright patch sit in different places. Bottom: after a 160 px correction, the same features line up.](img/rev_registration.jpg)

The full method, with the mathematics and a worked example, is in
[`alignment_method.md`](alignment_method.md).

The alignment model is a single translation per channel pair. That is
justified for imaging at range: measured rotation between channels is about
0.1° and scale about 1.005, both negligible. On this capture the correction
was 160 px and it lifted the correlation between the two channels from 0.74
to **0.98**.

## Where a single translation is not enough

It only works when everything in the scene is at roughly the same distance.
The cameras are 40 mm apart, so nearer objects shift more between channels
than distant ones, and no single translation can satisfy both at once.

Searching exhaustively for the best possible shift — not merely the one our
estimator picks — shows how sharp the distinction is:

| Scene | Best achievable single-shift alignment |
|---|---|
| Foliage about 1 m away, on the bench | NCC **0.483** |
| Ground from the drone, 15–20 ft | NCC **0.926** |

At altitude the ground is effectively a flat plane and translation is the
right model. At arm's length it is not, and the residual is parallax, which
no better estimator can remove.

This matters for close-range burn tests: it is fine when the fire and its
backdrop sit at similar distance, which the current burn setup arranges. It
is not fine pointed at a scene with real depth variation a metre away. If
that case becomes important, it needs per-pixel registration rather than a
better global fit.

Each fit is reported with a before-and-after correlation score so it shows
its own work, and frames too washed out or too featureless to align
reliably are flagged rather than silently accepted.

# Stage 4 — K-line index

**In:** an aligned, corrected triplet.
**Out:** one number per pixel — the potassium line strength — as a map, plus
a three-point spectral plot for any selected region.

Two filter sets are standardised, both Thorlabs hard-coated bandpass,
10 nm FWHM. The long-range set is the one on order and is also serviceable
close in:

| Set | Continuum | On-line | Continuum | Use |
|---|---|---|---|---|
| Long range | 750 nm | 770 nm | 780 nm | Flight, and adequate close range |
| Close range | 760 nm | 770 nm | 780 nm | Driveway and lab work |

The move from 760 nm to 750 nm for the long-range set came out of
D. Koenig's analysis of hyperspectral imagery of real wildfires.

This is a bracketing design: the two reference channels sit either side of
the line, so the continuum underneath it can be interpolated rather than
assumed. Since 770 nm sits two-thirds of the way from 750 to 780:

$$C_{770} = \tfrac{1}{3}\left(S_{750} + 2 S_{780}\right)
\qquad\qquad
\delta_{77} = \frac{S_{770} - C_{770}}{C_{770}}$$

The index is the *fractional excess* of the on-line channel over the
interpolated continuum, so it is signed and centred on zero:

- $\delta_{77} > 0$ — the 770 nm channel carries light the continuum cannot
  explain. That is the potassium signature.
- $\delta_{77} = 0$ — no emission.
- $\delta_{77} < 0$ — the on-line channel is dimmer than the continuum
  predicts.

Evaluated independently at every pixel, so the output is a map rather than a
single number. Implemented in `tools/k_index.py`.

**Validation.** Against synthetic data with a continuum deliberately sloping
across the band (1.00 / 0.90 / 0.85 at 750 / 770 / 780) and a known 0.30
emission patch, the median $\delta_{77}$ comes out **+0.000** — the slope is
removed exactly — and the 99th percentile **+0.293**, recovering the injected
signal. That is precisely the case a single off-line reference gets wrong and
bracketing gets right.

# How this compares with the Resonon

The Pika XC2 is a true imaging spectrometer: hundreds of contiguous bands at
roughly 1–2 nm resolution, which is why it resolves the potassium doublet as
two distinct peaks 3.4 nm apart.

**This instrument cannot reproduce that, and will not.** A 10 nm filter
integrates both K lines together with the continuum around them into a single
number. Three points is not a spectrum, and the doublet can never be
resolved.

What it can do is different rather than lesser:

| | Pika XC2 | This payload |
|---|---|---|
| Spectral sampling | hundreds of bands, 1–2 nm | 3 bands, 10 nm |
| Resolves the K doublet | yes | no |
| Image formation | pushbroom — scans to build a frame | snapshot, full 2D field at once |
| Output | spectrum per pixel | K-line index map over the whole scene |
| Cost, mass, power | laboratory instrument | three cameras and a Pi |

The claim being tested is not that three cameras match a spectrometer. It is
that three cameras, correctly calibrated, can detect *the same fire* the
spectrometer detects — at a cost and weight that a drone can carry routinely.

**A side-by-side against the Pika on the same burn is the single most
valuable measurement available to this project**, and it is worth arranging
before anything else on the list below. It is the experiment that either
supports the thesis or does not.

## The risk worth stating up front

The potassium lines are intrinsically narrow — well under a nanometre even
with broadening in a flame. A spectrometer at 1–2 nm resolution concentrates
that energy into one or two bands, which is why the spikes are so clear in
the Pika data. A 10 nm filter spreads the same line energy across ten
nanometres of mostly continuum, **diluting the contrast by roughly five to
ten times** relative to what the Pika sees.

That does not mean it will not work. It means the signal to look for is a
modest percentage excess in the 770 nm channel, not an obvious spike — and it
is why the calibration work above matters as much as it does. A 2% channel
error is tolerable against that signal; a 35% one would not be.

# What is not solved

**Capture is sequential, and slow.** About 2 seconds for all three channels.
On a moving aircraft the scene shifts between them, which is what registration
exists to correct — but registration can only correct a global shift, and at
close range different depths move by different amounts. In the 2026-08-29
flight data, channel offsets reached several hundred pixels.

The most promising route out of this is that these camera modules carry
**hardware trigger and strobe pins**. If all three can be fired from one
trigger they expose simultaneously, which does not merely reduce the
inter-channel displacement but eliminates it, and with it the need for
per-triplet registration on a moving platform. This has not been attempted.

**Boresight alignment — measured, and it is good.** An earlier version of
this document claimed one camera pointed about 120 px off and that cropping
to the common region cost a quarter of the sensor. That was wrong. The
figure came from bench captures a metre away, where the offset is parallax
from the 40 mm baseline, not a mounting error.

Measured at distance, where parallax collapses, the three cameras are
co-boresighted to a fraction of a pixel. Across 30 usable airborne triplets,
**23 needed no correction at all** — typical residual 0.1 to 0.8 px. The
seven that did need one needed a large one (100–790 px), and those are
captures where the aircraft moved during the sequence.

So the fixed geometry is not the problem, and the common overlap at range is
essentially the full frame. The only thing displacing the channels is
platform motion during the ~2 s capture cycle.

**Flight radio link.** During the August flight the WiFi control link became
unusable as soon as the aircraft gained altitude. Ground testing at range is
needed to separate antenna blockage from interference.

# Status summary

| Item | State |
|---|---|
| Three-camera capture, IMX296 global shutter mono | working |
| Live focus through the web interface | working |
| Web interface: capture, gallery, settings, timer | working |
| Flat-field and dark correction | **tool built and validated; awaiting a valid flat** |
| Channel registration | working |
| K-line index (δ₇₇) | **built and validated against synthetic data**; unproven on real data until filters are fitted |
| Narrowband filters (750 / 770 / 780 nm) | on order |
| Fire response | **not tested — no filters** |
| Simultaneous capture via hardware trigger | not attempted |

# Next steps, in order of value

1. **Fit the filters and re-derive the flat-field calibration through them.**
   Nothing about the science is demonstrated until this happens.
2. **Image a burn alongside the Pika XC2.** The comparison that tests the
   whole premise.
3. **Investigate hardware-triggered simultaneous capture.** Would remove the
   dominant error source on a moving platform.
5. **Characterise the radio link** on the ground at range before the next
   flight.
