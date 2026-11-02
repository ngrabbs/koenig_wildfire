# Offline Option A: shared registration, P and integrated brightness I

Research diagnostics for **stored** 770/750/780 nm triplets. No camera, CAN,
timer, Jetson service, fire threshold or classifier is connected. Every report
keeps `production_decision=NOT_CALIBRATED` and `scientific_decision=UNKNOWN`.
This is a new offline adapter, **not** the disabled B3 experiment or its safety
qualification. It does not provide a hard resource-isolation guarantee.

## Install and run

From the repository root, use Python **3.12.14** and an isolated environment:

```powershell
py -3.12 -m venv .venv-optiona
.\.venv-optiona\Scripts\python.exe -m pip install -r research/anchor_g2/requirements.txt
.\.venv-optiona\Scripts\python.exe -B -m unittest research.anchor_g2.test_adapter -v
.\.venv-optiona\Scripts\python.exe -B -m research.anchor_g2 C:\NickData\triplet.json
```

Confirm the interpreter patch version is 3.12.14. Do not silently substitute a
different native build if the pins are unavailable. The distribution version
5.0.0.93 reports `cv2.__version__` as 5.0.0. Cross-platform/native-build numerical
equivalence is not established by these package versions alone.

The CLI reads inputs and prints JSON to stdout. Exit 0 means eligible numerical
diagnostics; exit 2 means `UNKNOWN`. Redirect stdout only to a **new** report
outside the input files; shell redirection can overwrite files. Nothing writes
back to images, correction maps, masks or manifests. Use `-B` to avoid bytecode
writes. Input hashes in the report bind the actual bytes read.

## Input contract

Start with `example_request.json`; replace every placeholder and provide all
referenced files. Paths are relative to the request JSON (absolute paths also
work). SHA-256 is lowercase hexadecimal of the exact file bytes; on PowerShell:

```powershell
(Get-FileHash C:\NickData\770.png -Algorithm SHA256).Hash.ToLowerInvariant()
```

* Three distinct cameras and correction ports, explicitly mapped to 770, 750,
  780 nm. Processing order is always 770,750,780 and 770 is the geometry anchor.
* Lossless **linear uint8 monochrome PNG/TIFF** in native sensor coordinates,
  identical dimensions, minimum 64x64, at most 4,000,000 pixels. No crop, resize,
  tone mapping, already-warped or already-corrected input. This minimum adapter
  intentionally rejects JPEG, RGB and higher-bit-depth inputs; converting a JPEG
  to PNG does **not** make its radiometry eligible. Encoding provenance is an
  operator responsibility, not something the decoder can prove.
* Correction NPZ with `gain_<port>` and `dark_<port>` arrays. Shapes must match;
  all values finite and gains positive. The hash-bound correction provenance
  must explicitly attest measured dark correction and linear 8-bit DN, and match
  camera, filter, wavelength, optical configuration, exposure and gain for each
  capture. Equal positive exposure and gain across channels are required.
  The Phase 4 archive is neither included nor assumed compatible. Missing
  provenance is `UNKNOWN`, never an identity-map fallback.
* Boolean `.npy` source and background masks on the **770 output grid**, same
  shape as images; disjoint, nonempty and fully inside valid common support.
* `example_eligibility.json` documents the required independent support evidence.
  It binds image hashes (770,750,780 order), mask hashes and the exact transforms.
  Evidence must establish simultaneous acquisition/optical compatibility,
  independent alignment residual, source containment with an aperture margin,
  representative background and selection independent of the P/I results.
  Supplied assertions are recorded as operator evidence, **not software-verified
  physical truth**. Do not set fields true just to obtain an output.

A first attempt with incomplete eligibility can return the fitted geometry but
`UNKNOWN` measurements. Use independently measured control points/source extent
to assess that geometry before preparing the bound evidence. NCC/phase response
alone is not an independent alignment measurement. Do not derive the aperture or
eligibility from the desired spectral result. Small-fire dilution, parallax and
residual misalignment can remain substantial for three separated cameras.

## Computation and outputs

Correction `(raw-dark)*gain` occurs **before** registration in native coordinates,
using the frozen Phase 4-compatible clipped 8.8 fixed-point representation.
The frozen phase preparation and translation fit are used for both consumers;
signal and validity masks use the same transforms, linear sampling and erosion
as B2. The common-support engineering gate is 0.6. It is not a detection threshold.

P calls the unchanged frozen measurement function: continuum `(750+2*780)/3`,
excess `770-continuum`, and fractional excess where continuum >=4 corrected DN.
The 4-DN floor is the frozen numerical floor, not a fire threshold. P summaries
cover valid common pixels, not just the source; maps are reported as hashes and
counts, not exported image files.

I uses the unchanged terminal arithmetic block extracted from frozen `primary_i`:
per-channel source sum minus source-pixel count times background mean, then the
same continuum weights and additive/fractional excess. The generated-only
containment gate is **not** used or impersonated for real data. The new adapter
checks independent evidence and support before calling the extracted arithmetic.
If P is below its floor or exceeds its independent alignment tolerance but I is
eligible, I is still reported as a diagnostic. Invalid source/background support
keeps overall status UNKNOWN. A positive I ratio is never a fire classification.
`supported_ratio` remains null: covariance/denominator uncertainty is unresolved.

See `example_report.json` for a full failure-envelope example. Successful reports
add geometry checks/transforms, P summaries and I gross/background/net values.
Reason codes identify missing provenance, incompatible correction, geometry
rejection, unsupported apertures or numerical ineligibility.

## Verification and provenance

`upstream_source_manifest.json` records the frozen B2 identities. Files in `frozen/`
are byte-identical copies; `integrated_arithmetic.py` retains the exact original
terminal I block. Tests verify these identities and compare selected generated
unit cases against the unchanged generated-only I function. They cover I-only
eligibility, identity registration, flat rejection, malformed inputs, hash
rejection and read-only end-to-end behavior. This small regression subset is
**not** the original 24-case B3 run and does not enable its policy.

`execution_policy.json` remains disabled. The frozen session API is not used.
The retained runtime helper module is needed by frozen array/hash code; its
host-specific B3 configuration and resource supervisor are never invoked.
There is no VM safety infrastructure, captured imagery or calibration data here.
See `PROVENANCE.md` for development-only historical context, not wildfire accuracy.
