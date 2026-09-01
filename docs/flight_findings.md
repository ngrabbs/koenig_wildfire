# Flight Findings — TL-002

*Cubesat@MSU · Version 1.0 — 1 September 2026 · for payload v0.3*

## What this document is

What the 2026-08-29 drone flight taught us about the instrument, and the
decisions that came out of it. It exists so the reasoning behind the current
design is recoverable rather than folklore.

**Source data:** 122 frames / 41 capture events, logged as TL-002.

It does not explain how channel alignment works. That moved to
[`alignment_method.md`](alignment_method.md), which covers the mathematics
and a worked example, with a practical guide in
[`alignment_walkthrough.md`](alignment_walkthrough.md).

## Root cause: the capture cycle is too slow

> **Correction, 2026-08-31 — the timings in this section are invalid.**
> Every cycle-time number below was measured while a separate bug was
> live: holding three Picamera2 instances open at once broke video-mux
> routing, so two of the three "captures" in each cycle were re-reads of
> an already-selected camera and did no mux switching at all. The
> measurements were real, but they were not measuring three-channel
> capture.
>
> Correct three-channel capture, one camera opened at a time, costs about
> **1.95 s** — slower than the pre-optimisation code, not faster. The
> dominant term is `Picamera2.close()` at 0.404 s per camera. The
> resolution and encode findings below still hold in direction; only the
> absolute numbers are wrong. See the commit `capture: fix three-channel
> captures all returning the same frame` for the full account.

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

## Exposure: most of the flight data is clipped

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

### Two consequences

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

## Filters — ordered 2026-08-29

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

## What we are going to do, and why

In priority order.

#### 1. Cut the capture cycle time

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

**Measured result, 2026-08-29 — superseded, see the correction above.**
These were timed end-to-end through the daemon's `/capture` endpoint, but
against the broken mux routing described in that note:

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
> install — an existing `~/.payload/settings.json` keeps whatever it already
> stored. The rig at `192.168.1.46` has been switched to 2028 × 1520
> explicitly. Any other unit needs the same change, from the settings page
> or by deleting the stored resolution key.

#### 2. Register per triplet, not once

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

**Implemented** as `tools/register_triplets.py` (2026-08-29). Groups frames
into triplets by capture-event stem, estimates the shift per pair, warps onto
a reference channel, crops to the region valid in all three, and writes a
per-triplet report. `--composite` emits a false-colour RGB QA image: if
registration worked the edges are grey, and coloured fringing means residual
misalignment.

Two things that came out of building it, both worth knowing:

- **`cv2.phaseCorrelate`'s own `response` value is not a usable confidence
  measure on this data.** Event `20260829_120648_759` scores 0.104 while
  genuinely improving alignment (NCC 0.331 → 0.694), and `20260829_121016_613`
  scores 0.009 on the best-exposed frames of the whole flight. The tool
  instead applies the shift and measures normalised cross-correlation over the
  valid overlap — slower, but it measures the thing we care about rather than
  a proxy for it. The report prints NCC before and after, so every fit shows
  its own work.

- **A featureless frame aligns to a confident-looking zero.** Blown-out frames
  correlate to NCC ~1.0 against each other while carrying no information, so
  there is a separate gate on gradient energy. Without it the tool reports
  success on exactly the frames that are worthless.

Run against the TL-002 flight set: **20 registered, 20 low-confidence, 1
incomplete** of 41 events. The low-confidence half is the clipped data — which
is the exposure problem above, surfacing again as an inability to register.

#### 3. Operational: capture in a hover

Costs nothing, available immediately, and directly reduces the displacement
the aligner has to solve. Start a timer capture, hover over the target, land.
Do not fly a translating pass and expect clean triplets.

## What we are deliberately not doing

| Not doing | Why |
|---|---|
| Panorama stitching | The frames are 95–99% overlapped. There is no mosaic to build. |
| Homography / projective warp | Measured rotation ≈ 0.1°, scale ≈ 1.005. Translation-only is sufficient and far more robust on low-texture frames. |
| One-time factory calibration | Motion dominates geometry by an order of magnitude. A fixed offset would be wrong on every frame. |
| Enabling auto-exposure | Each camera would pick its own exposure for the same scene and the channel ratio would be meaningless. Exposure must stay fixed and shared. |
| Lens distortion correction | Not yet measured, and swamped by everything above. Revisit after the cycle-time and exposure work, if residual misalignment justifies it. |
| Chasing the 135 ms/channel floor | That is pipeline fill after `start()`, and the CSI switch forbids keeping more than one camera streaming. Not worth attacking until the rest is done. |

## Open questions

- **Does the aligner survive real spectral differences?** Everything measured
  here is broadband — all three channels saw the same light. Once filters are
  fitted the channels genuinely differ, and feature matching across bands is
  harder. 750 / 770 / 780 nm are within 4% of each other so scene structure
  should be nearly identical, but this needs testing rather than assuming.
- **How much does a hover actually help?** Untested. One session comparing
  hover captures against translating-pass captures would quantify it.
- **Is 0.45 s good enough?** Unknown until we fly it.

## Update 2026-08-29 — both remaining levers just became available

The two fallbacks named above were written when the IMX296 could not run
through the mux at all. That is no longer true, and it changes the outlook
on this whole document.

**Global shutter is now available.** The IMX296 plate is fitted and working
(see [`../pi/dtoverlay/README.md`](../pi/dtoverlay/README.md)). Global
shutter removes rolling-shutter skew *within* each frame — the distortion
that gets worse the faster the aircraft moves. It does nothing for
displacement *between* channels, which is still the sequential-capture
problem, but it removes one of the two motion artefacts outright.

**Hardware synchronisation may no longer need the Camarray.** The modules
fitted are InnoMaker CAM-IMX296RAW, which carry **dedicated hardware
trigger input and strobe output pins**, supported on Raspberry Pi. If all
three cameras can be driven from one trigger, the three channels expose
*simultaneously* rather than 0.45 s apart — which does not merely reduce
inter-channel displacement, it eliminates it, along with the entire need
for per-triplet registration on a moving platform.

That would resolve the core finding of this document. It is the single
highest-value thing to investigate next, and it is worth doing before
tuning anything else here.

Unknowns to settle first: whether the Arducam v2.2 mux passes a trigger
signal through at all (it is a CSI switch, so the trigger likely has to be
wired directly to each module, bypassing the mux), and whether the three
sensors can be read out sequentially after a simultaneous exposure — the
CSI switch still only carries one camera's data at a time, so the frames
would need to be held on-sensor and read out one after another.

Reference: <https://github.com/INNO-MAKER/cam-imx296raw-trigger>
