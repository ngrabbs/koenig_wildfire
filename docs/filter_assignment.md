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
"wavelengths": { "0": 770, "1": 750, "2": 780 }
```

**As fitted on 2026-09-17** — this is the current arrangement, and note it is
*not* in ascending order:

| Camera | Filter | Role |
|---|---|---|
| cam0 | **770 nm** | **on-line** — the K I doublet |
| cam1 | 750 nm | continuum, below the line |
| cam2 | 780 nm | continuum, above the line |

Set it from **Camera settings → Filter fitted to each camera** in the web
interface. Nothing else needs editing — the capture filenames, the focus-mode
labels and the settings page all read from this one value.

> The numbers in `CHANNELS` in `pi/daemon/camera.py` are a fallback for a
> fresh install only. Editing them does not change what a capture is named.

## The two standard sets

| Set | Filters | Use |
|---|---|---|
| Long range | 750 / **770** / 780 nm | Flight. Also serviceable close in |
| Close range | 760 / **770** / 780 nm | Driveway, MCC lab |

Which camera carries which is a separate question, answered by the table
above — there is no requirement that they ascend with port number, and
currently they do not.

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

## Analysis reads the mapping from the filenames

`tools/k_index.py` does not assume an arrangement. It reads each capture's
wavelengths from its own filenames, so a session shot under a different
filter set analyses correctly with no flags. It also refuses any triplet
whose wavelengths are not one of the two standard sets, which is what stops
pre-filter data being fed through the index by accident.

`--roles 0=770,1=750,2=780` overrides, for the case where a filename is known
to be wrong.

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
