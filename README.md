# Narrowband K-line wildfire detection payload

A candidate payload for **EMBER** (Emission Monitoring for Burn Event
Recognition), the MSU CubeSat programme. EMBER is the satellite; this is one
instrument that could fly on it.

Burning vegetation releases neutral potassium, which emits at a pair of
near-infrared lines (766.5 and 769.9 nm). Three monochrome cameras image the
same scene through different narrowband filters: one centred on that emission
and two either side of it. Comparing what the on-line channel sees against
the continuum interpolated from the two reference channels isolates the
potassium signal — a wildfire fingerprint a small silicon-sensor payload can
detect without thermal-IR optics.

The premise being tested is not that three cameras rival a hyperspectral
imager. It is that three cameras, correctly calibrated, can detect **the same
fire** a hyperspectral imager detects, at a cost and weight a drone can carry
routinely.

The physics is in [`docs/k_line_primer.md`](docs/k_line_primer.md).

---

## Start here

New to the project? Read in this order:

1. **[`docs/pipeline_review.md`](docs/pipeline_review.md)** — what the
   instrument does today, stage by stage, with real captures. The best single
   overview. (`make review-pdf`)
2. **[`docs/k_line_primer.md`](docs/k_line_primer.md)** — why potassium, and
   why these wavelengths.
3. **[`docs/payload_build.md`](docs/payload_build.md)** — the physical
   hardware: plates, optics, power, and what is still missing.
4. **[`docs/operator_manual.md`](docs/operator_manual.md)** — how to actually
   fly and operate it.

Then, depending on what you are working on:

| If you are… | Read |
|---|---|
| Setting up a fresh Pi | [`docs/hardware_setup.md`](docs/hardware_setup.md) |
| Touching the capture code | [`docs/architecture.md`](docs/architecture.md) |
| Working on image alignment | [`docs/alignment_method.md`](docs/alignment_method.md) — the method, with the maths |
| Why alignment came out the way it did | [`docs/channel_registration.md`](docs/channel_registration.md) — the flight findings |
| Fighting the camera multiplexer | [`pi/dtoverlay/README.md`](pi/dtoverlay/README.md) |

---

## Current state

**The instrument works. The science has not been demonstrated.**

Working and field-tested: three IMX296 global-shutter monochrome cameras
capturing through an Arducam multiplexer, a Flask web interface for capture /
gallery / settings / timed capture, live focus streaming, AP-fallback wifi,
and auto-start on boot. Flown on a drone in August 2026.

Not yet done, in the order it matters:

- **The narrowband filters are not installed.** Ordered — Thorlabs
  `FBH750-10`, `FBH770-10`, `FBH780-10`, 10 nm FWHM. Until they arrive every
  camera sees identical broadband light and **no potassium measurement
  exists**. All imagery so far is engineering data.
- **The K-line index is written but unproven.** `tools/k_index.py` is
  validated against synthetic data with a known answer, but with no filters
  fitted every channel sees identical light, so on real captures it returns
  noise about zero.
- **Flat-field calibration is built and validated but not applied** — the
  only flat captured so far is invalid (see below).
- **Capture is sequential and takes ~2 s** for all three channels, which is
  the dominant error source on a moving aircraft.

> **Careful with the filenames.** Captures are named `..._cam0_762nm.jpg` and
> so on, but those wavelengths are the *old* intended assignment and no
> filters are fitted. They are labels, not measurements. The rename to
> 750 / 770 / 780 lands when the filters do, so that the optical change and
> the label change happen together and no data set is ambiguous.

---

## Getting set up

### The analysis tools

The tools in `tools/` run on your laptop, not the Pi:

```bash
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
```

`.venv/` is gitignored. On Debian/Ubuntu a system-wide `pip install` will be
refused by PEP 668 — use the venv rather than fighting it.

### Talking to the payload

The Pi runs two services, started automatically on boot:

| Service | Port | Job |
|---|---|---|
| `payload-daemon` | 8001, loopback only | Owns the cameras. Capture, settings, focus. |
| `payload-webui` | 8000, all interfaces | The page the operator's browser loads. |

Point a browser at **`http://payload-pi.local:8000`**. In the field, if no
known wifi is available the Pi raises its own access point — SSID **`satnet`**,
password **`cubesat1`** — and the interface is at
**`http://192.168.4.1:8000`**.

### Running the tests

```bash
.venv/bin/pip install flask          # the webui tests need it
.venv/bin/python pi/tests/test_system_routes.py
```

No hardware required.

---

## The processing chain

```
capture  ──▶  flat_field.py  ──▶  register_triplets.py  ──▶  k_index.py
 3 raw         instrument            channels on a            delta77, the
 frames        response out          common pixel grid        measurement
```

The order matters: flat-fielding must come **before** registration, because
the correction map describes the sensor's own pixels and registration moves
the image out from under it.

```bash
# derive a correction (once per optical configuration)
.venv/bin/python tools/flat_field.py check ./flat          # is the flat usable?
.venv/bin/python tools/flat_field.py calibrate --flat ./flat --dark ./dark -o correction.npz

# then per capture session
.venv/bin/python tools/flat_field.py apply ./raw --correction correction.npz -o ./corrected
.venv/bin/python tools/register_triplets.py ./corrected -o ./aligned --composite
.venv/bin/python tools/k_index.py ./aligned -o ./kindex
```

The measurement itself is the K-index, δ₇₇ — the fractional excess of the
on-line channel over the continuum interpolated from the two references:

```
             S770 - (1/3)(S750 + 2*S780)
  δ77   =   -----------------------------
                (1/3)(S750 + 2*S780)
```

Positive means the 770 nm channel carries light the continuum cannot
explain. Zero means no potassium emission.

Both tools explain their own output and flag captures they could not process
rather than failing quietly.

### A warning about flat fields

A flat must be **featureless and unclipped**. Two attempts have already
failed in instructive ways:

- **Sky through broken cloud** — 100% saturated, and the cloud structure would
  have been baked into every corrected frame as false vignetting.
- **Paper resting on the lenses** — a diffuser touching the front element
  exaggerates the lens falloff and reported 6–37% *more* vignetting than the
  lens sees on a real scene. Correcting with it made the channels agree
  *worse*.

Use a distant uniform source: even overcast, or an evenly lit white panel
several feet away.
Better still, use a material made for the job. **Spectralon** and
**Fluorilon** are engineered diffuse-reflectance standards and give excellent
uniformity; **Teflon sheet** is far cheaper and a reasonable substitute.
D. Koenig's group uses Teflon for exactly this. Light it evenly, fill the
frame with it, and keep it far enough away to be out of the near field.

`flat_field.py check` will tell you before you waste a session on it.

---

## Repo layout

| Path | What's here |
|---|---|
| `pi/daemon/` | Capture daemon — owns the cameras, mux, scheduler |
| `pi/webui/` | Flask web interface the operator's browser talks to |
| `pi/shared/` | Settings schema shared between daemon and UI |
| `pi/dtoverlay/` | Custom device-tree overlay — the IMX296-through-mux work |
| `pi/network/` | AP-fallback wifi installer |
| `pi/systemd/` | Unit files for auto-start on boot |
| `pi/tests/` | Tests. No hardware needed. |
| `tools/` | Ground-station analysis: registration, flat-field, PDF rendering |
| `docs/` | All documentation, markdown → PDF via pandoc |
| `hardware/` | Filter-holder STLs and SolidWorks sources |
| `assets/` | Build photos from the earlier hardware generations |
| `legacy/` | Archived pre-rewrite code (see `legacy/README.md`) |

## Building the documentation

Every document renders to PDF through pandoc with a mermaid preprocessor:

```bash
make review-pdf      # the pipeline review — the best overview
make pdf             # the operator manual
make all-pdfs        # everything
make help            # list the targets
```

Requires `pandoc`, `lualatex`, and `pip install mermaid-py`. Output lands in
`docs/build/`, which is gitignored.

## Hardware generations

| | | |
|---|---|---|
| v0.1 | Apr 2025 | 2U CubeSat stack, ESP32 firmware |
| v0.2 | May 2025 | 3 × Pi Zero 2 W + ESP32/SIM7600, LTE to a Flask C2. Tagged `v0.2-his-final-snapshot` |
| v0.3 | Aug 2026 | Single Pi 4 + Arducam mux + 3 × IMX296, local web interface. Tagged `v0.3-wildfire-pi4` |

## Where the open work is

Roughly in order of value to the project:

1. Fit the filters and re-derive the flat-field calibration through them.
   Nothing about the science is demonstrated until this happens.
2. Image a burn alongside Dr. Koenig's Resonon hyperspectral imager. The
   comparison that tests the whole premise.
3. Investigate hardware-triggered simultaneous capture — these camera modules
   have trigger and strobe pins, which would remove the dominant error source
   on a moving platform.
6. Characterise the wifi link range on the ground; it became unusable at
   altitude during the August flight.
