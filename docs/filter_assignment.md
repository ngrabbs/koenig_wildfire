# Filter assignment

*Which filter is on which camera, and how to prove it · for payload v0.3*

The K-index subtracts one channel from a blend of the other two. Get the
assignment wrong and the arithmetic still runs, still produces a plausible
map, and is meaningless — there is nothing in the data that will tell you.

So this is recorded in one place, read live at capture time, and **verified
against the hardware before every session that matters.**

## Where it is recorded

`settings.json` on the payload, under `wavelengths`:

```json
"wavelengths": { "0": 750, "1": 770, "2": 780 }
```

Set it from **Camera settings → Filter fitted to each camera** in the web
interface. Nothing else needs editing — the capture filenames, the focus-mode
labels and the settings page all read from this one value.

> The numbers in `CHANNELS` in `pi/daemon/camera.py` are a fallback for a
> fresh install only. Editing them does not change what a capture is named.

## The two standard sets

| Set | cam A | cam B | cam C | Use |
|---|---|---|---|---|
| Long range | 750 nm | **770 nm** | 780 nm | Flight. Also serviceable close in |
| Close range | 760 nm | **770 nm** | 780 nm | Driveway, MCC lab |

770 nm is always the **on-line** channel — it carries the potassium doublet
at 766.49 and 769.90 nm. The other two are continuum references, one either
side, and the continuum under the line is interpolated between them.

The move from 760 to 750 for the long-range set came out of Dr. Koenig's
analysis of hyperspectral imagery of real wildfires.

## Verifying it — do this before a burn

Two minutes, and it is the only thing that actually proves the mapping.
A label agreeing with another label proves nothing.

**1. Set the mapping** in the web interface to match the filters you fitted.

**2. Cover one lens.** Use your hand or a lens cap. Pick the camera you
believe is 770 nm.

**3. Take one capture.** Open the gallery.

**4. Check which file is dark.** If you covered the camera you believe is
770 nm, then `..._cam1_770nm.jpg` — or whichever camera number carries 770 —
must be the dark one.

- **The dark file is named for the wavelength you covered** → correct.
- **A different file is dark** → the mapping is wrong. The file that went
  dark tells you which camera number you actually covered. Fix the setting
  and repeat.

**5. Repeat for a second camera.** Two confirmed assignments plus three
distinct filters fixes the third by elimination.

Record the result in the test log for the session. "Verified by occlusion"
with the filenames is enough.

## Why a label is not evidence

The failure this guards against is silent. Suppose 770 is physically on
camera 2, but the setting says camera 1:

- every capture still produces three files;
- the filenames still read 750, 770, 780;
- the focus buttons still say 750, 770, 780;
- alignment still works, because it does not care what the channels mean;
- the K-index still computes, because it just reads the file named 770.

What you get is the *continuum* channel treated as on-line and a *reference*
channel treated as continuum. The index comes out systematically wrong, in a
way that looks like data rather than like an error. No downstream check
catches it. The occlusion test catches it in two minutes.

## After a burn test

The filenames are the only record of the mapping once the images leave the
payload. Two things worth doing before the data goes anywhere:

- **Copy the session to its own folder.** Mixing sessions in one directory
  has already caused confusion once (TL-002 anomaly A3).
- **Note the mapping in the log**, even though the filenames carry it. If a
  filename is ever renamed by hand, the log is the fallback.

## History

Captures before 2026-09-17 are named `762` / `766` / `770`. Those were the
*intended* assignment under a superseded filter scheme, and **no filters were
fitted for any of them** — every channel recorded the same broadband light.
Treat those wavelengths as channel identifiers, not measurements, and do not
compute an index from that data.
