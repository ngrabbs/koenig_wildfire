# Nick: stored three-camera test-day processor

Research only. Production stays **NOT_CALIBRATED**, science **UNKNOWN**. This package
never captures images, classifies fire, or authorizes formal B3. Matte dark material
can simplify a background; it does not establish linear radiometry or correct timing.

## Install once

From this repository root, use Python **3.12.14**:

```powershell
py -3.12 -m venv .venv-optiona
.\.venv-optiona\Scripts\python.exe --version
.\.venv-optiona\Scripts\python.exe -m pip install -r research/anchor_g2/requirements.txt
.\.venv-optiona\Scripts\python.exe -B -m unittest research.anchor_g2.test_adapter research.anchor_g2.test_exploratory_jpeg -v
```

Pins: NumPy2.3.5 and opencv-python-headless5.0.0.93 (reports OpenCV5.0.0).
Different builds/platforms are not independently numerically qualified. No VM,
shift sweep, historical dataset runner or B3 supervisor is needed.

## Inputs and first geometry check

Keep originals outside the repository. Copy `example_jpeg_request.json` to
`C:\NickData\triplet.json`. Set paths to your actual JPEGs, keeping cam0=770,
cam1=750, cam2=780 nm. Paths resolve relative to the manifest. Record each actual
exposure, gain, AE setting, original timestamp/time basis, focus, filter/optics,
illumination and known capture interval in `acquisition`. Leave genuinely unknown
values null; the processor records them without pretending to verify them.

Fill hashes after editing paths (PowerShell; UTF-8 without BOM):

```powershell
$p = 'C:\NickData\triplet.json'
$m = Get-Content -Raw -LiteralPath $p | ConvertFrom-Json
foreach ($c in $m.channels) {
    $f = if ([IO.Path]::IsPathRooted($c.path)) { $c.path } else { Join-Path (Split-Path $p) $c.path }
    $c.sha256 = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
}
[IO.File]::WriteAllText($p, ($m | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
```

Leave `eligibility` and `provisional_regions` null for a geometry-only check.

```powershell
.\.venv-optiona\Scripts\python.exe -B -m research.anchor_g2 C:\NickData\triplet.json --exploratory-jpeg --output C:\NickData\geometry.json
```

`--output` refuses an existing file and never creates parent directories. Use a
fresh output name for each run. Without it, JSON goes to stdout. Exit2 means UNKNOWN,
including usable provisional diagnostics without independent eligibility; exit0
means the existing numerical-eligibility path returned DIAGNOSTIC_ONLY, not validated
science. A rejected registration has no forced fallback or spectral measurement.

## Define regions independently, then process

Inspect continuum750/780 views, physical holder, matte background and stationary
reference features before consulting770 excess. Define rectangles on the full-sized
770 reference grid AFTER shared registration, in original image orientation:
`[x0,y0,x1,y1]`, zero-based, right/bottom exclusive. Do not use a camera's native750
coordinates as reference-grid coordinates. Keep source/background disjoint.

Replace `provisional_regions: null` with your own coordinates and rationale:

```json
"provisional_regions": {
  "schema": "PROVISIONAL_RECTANGLES_V1",
  "source": [640, 380, 760, 500],
  "background": [800, 380, 920, 500],
  "selection_rationale": "REPLACE: physical source boundaries, background material, coordinate basis and unresolved containment"
}
```

**Those coordinates illustrate syntax, not a recommended mask for your test.**
Freeze your actual region choices before numerical comparison. Use comparable
physical regions for fire-off/fire-on; record any incomplete flame containment,
shadows, reflections or source spill. The tool does not supply or approve masks.
If any requested pixel is outside common support (including clipped/guard pixels),
provisional region measurements stay unavailable; it does not silently trim the ROI.

```powershell
.\.venv-optiona\Scripts\python.exe -B -m research.anchor_g2 C:\NickData\triplet.json --exploratory-jpeg --output C:\NickData\diagnostics.json
```

All outputs are in that JSON: channel ranges/clipping, registration matrices and
quality, common support, region definitions/counts/hashes, P numerical summaries,
and integrated gross/background/net values. No aligned image or pixel map files
are written. Frozen P provides whole-common-field valid-pixel summaries and map
hash descriptors; its `source_valid_count` is ROI-specific, its mean/median are NOT
ROI-only statistics. Use the same triplet/common geometry for comparison.

## Interpret cautiously

Without independent support, top-level `P`/`I` remain null and status UNKNOWN.
`provisional.schema=PROVISIONAL_JPEG_DIAGNOSTICS` holds separate unapproved numerical
results. `P_numerical` uses the unchanged legacy continuum>=4 floor;
`I_numerical` uses unchanged frozen integrated arithmetic and reports source sums,
background means, net signals, continuum and additive excess. Positive numeric
ratios do not establish resolved denominator uncertainty; `supported_ratio` stays
null. Inspect `net_to_gross_continuum`, negative channel signals and reason flags.
Very low or nonpositive continuum, saturation and background mismatch can make a
ratio uninformative. No new denominator threshold or fire threshold is introduced.

All JPEG quantities are code-value proxies, not radiance. Gamma/ISP, compression,
camera/filter response, calibration compatibility and sequential capture remain
unverified. Unit gain/zero offset only encode unchanged JPEG values for the frozen
pipeline. No supplied JPEG calibration is accepted silently. The strict linear-image
path is unchanged; see README.md. Independently justified hash-bound support can
use the existing eligibility contract described there; never assert synchronization
or source completeness merely to unlock P/I.

For this test, keep exposure, gain, AE, focus, filters and illumination unchanged;
record fire-off/on close together and preserve original timestamps. Use stationary
reference features near the source plane and avoid clipping. A near-black background
can leave too little texture for registration or too little continuum for ratios.
The historical cameras captured sequentially over roughly2seconds; document actual
timing rather than assuming simultaneous views of a flickering flame.

## Verified smoke example

The command shape above was exercised end-to-end with the existing200us control
20261014_155011_645, using explicit archive-member inputs and the previously defined
holder/background rectangles (not new test-day approvals). It returned accepted
engineering geometry and separate provisional numerical outputs, with UNKNOWN /
NOT_CALIBRATED and strict P/I unavailable. Original JPEGs were read-only. This is
software smoke verification, not wildfire accuracy or formal B3 qualification.
