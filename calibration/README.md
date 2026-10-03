# Calibration products

## `correction_20261003.npz`

The first valid flat field for this payload. Built 3 October 2026 from a
white PTFE (Teflon) panel at Dr. Koenig's.

```bash
tools/flat_field.py apply ./raw --correction calibration/correction_20261003.npz -o ./corrected
tools/register_triplets.py ./corrected -o ./aligned --no-crop
tools/k_index.py ./aligned -o ./kindex
```

**Apply it before registration.** The gain map is tied to the sensor's pixel
grid; registration warps the frame and the map would no longer describe the
pixels underneath it.

### How it was taken

| | |
|---|---|
| Source | White PTFE panel, direct sunlight, filling most of the frame |
| Flats | 10 triplets at 24000 µs, gain 1.0, AE off |
| Darks | 10 triplets at 24000 µs, lens caps on |
| Frame-to-frame drift | 0.14% across the ten |
| Noise after median | 0.16–0.18 DN |
| Smoothing | `--smooth 0.01`, the default |

Raw frames are on the payload under `~/payload_images/20261003_*`. They are
not in this repo and they are irreplaceable — worth archiving somewhere
durable.

### What it fixes

Each narrowband filter has its own response across the field, because an
interference filter in a non-collimated beam passes a wavelength that
depends on the angle light strikes it. Uncorrected, that makes
δ<sub>77</sub> depend on *where in the frame* a source happens to land.

Measured on a uniform target over the inner 80% of the field:

| | uncorrected | corrected |
|---|---:|---:|
| The flat it was derived from | 0.258 | 0.008 |
| **An independent second flat set** | **0.278** | **0.035** |

The second row is the one that counts — an 8× reduction on data the
correction was not built from. Applying a correction back to its own source
only bounds what it can do.

### Why a Teflon panel rather than sky

Equalising all channels to a common target forces δ<sub>77</sub> of the flat
source to exactly zero, so **whatever is used as the flat becomes the
instrument's zero point.**

Clear sky is a fine *spatial* reference but a poor *zero*: its spectrum is
steeply sloped and not known well enough to separate instrument response
from illumination. A neutral reflector under the same sunlight the target is
under is the defensible choice.

That this was the right call is visible in the data. At frame centre, before
any correction:

| Source | δ<sub>77</sub> at centre |
|---|---:|
| Clear sky, 17 Sep | −0.257 |
| **Teflon panel, 3 Oct** | **−0.010** |

A neutral reflector reads essentially zero. Most of the sky's −0.257 was the
sky, not the instrument.

### Effect on the 20 September detection

The detection does not depend on this correction — it rests on a
ratio-of-ratios, in which throughput cancels. Applying the flat *strengthens*
it slightly, which is the expected direction: the instrument is least
sensitive at 770, so correcting for that raises the on-line channel relative
to the continuum.

| Capture | raw | corrected |
|---|---:|---:|
| 1500 µs | +4.223 | **+4.625** |
| 2000 µs | +3.423 | +3.524 |
| 5000 µs | +0.600 | +0.664 |

> These were measured with a fixed 60 px aperture on each channel's own
> centroid, which is alignment-free. `docs/potassium_detection.md` quotes
> +7.27 for the 1500 µs capture from a tighter threshold-defined region.
> Both are strongly positive; δ<sub>77</sub> is **aperture-dependent for an
> extended source** and any quoted figure has to state its region.

### Known limitations

**The panel did not quite fill the frame.** Between 1.5% and 5.5% of pixels
per channel fell below 25% of the centre value — the panel edge, visible as
a dark arc on the right of 750 nm and in the corners of the others. Those
pixels hit the gain clamp (0.07–0.15% per channel) and their gains are not
trustworthy. **Treat the outer ~10% of the field as uncalibrated.** A second
attempt that day was worse, not better; the fix is to get the rig closer so
the panel over-fills, since the three cameras are splayed by the boresight
offset and all three must be covered at once.

**Valid only for this optical configuration.** Refocusing an M12 lens changes
the ray angles through the filter and moves the interference rings. If focus
is touched, or a filter is moved between cameras, this file is void and a new
flat is needed.

**Darks are effectively zero** and that is a real result, not a failure:
0.00006–0.0036 DN mean at 24000 µs. The sensor's black-level correction
handles the offset, so dark subtraction is close to a no-op here. Keep taking
them anyway — it costs a minute and an offset biases a ratio directly rather
than cancelling.

**Exposure-matched darks.** These darks are at 24000 µs. A science capture at
a very different exposure should get its own darks rather than reuse these.
