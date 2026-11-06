# Nick: three-camera JPEG testing modes

Research only: **NOT_CALIBRATED / UNKNOWN**. No capture-software change, classifier,
threshold, hardware access or formal B3 authorization. Start with repeated fire-off
and fire-on captures against the proposed uniform matte dark background, with
stationary reference features near the source plane.

## Install

From repository root, Python **3.12.14**:

```powershell
py -3.12 -m venv .venv-optiona
.\.venv-optiona\Scripts\python.exe --version
.\.venv-optiona\Scripts\python.exe -m pip install -r research/anchor_g2/requirements.txt
.\.venv-optiona\Scripts\python.exe -B -m unittest research.anchor_g2.test_adapter research.anchor_g2.test_exploratory_jpeg research.anchor_g2.test_modes -v
```

NumPy2.3.5; opencv-python-headless5.0.0.93 reports OpenCV5.0.0. Cross-platform/native
build equivalence is not qualified. No VM, historical experiment runner or sweeps
are needed. Use the existing stored captures; do not modify camera software.

## Select what to measure

| CLI mode | Image geometry | Measurement | Region requirement |
|---|---|---|---|
| `registered-boxes` (default) | Frozen ANCHOR_G2 aligns750/780 to770 | Existing registered P and background-subtracted integrated brightness | Shared source/background rectangles on the registered770 grid, or existing independent-support contract |
| `independent-boxes` | Native decoded images; no registration, warp or crop | Per-camera source sum minus its source count times its own background mean | Explicit separate source/background rectangles for EACH camera |
| `whole-frame` | Every native pixel; no registration/resize | Gross totals only; **no background subtraction** | No regions |

Each uses C=(S750+2*S780)/3 and D=S770-C. Native modes do not compute pixelwise P,
which needs corresponding geometry. Independent-box totals use each camera's own
pixel counts; unequal source areas are reported, never normalized automatically.
Whole-frame differences can reflect field of view, vignetting, pixel counts or
scene content. A small flame can be heavily diluted by the full-image background.

## Prepare one manifest per mode

Copy the appropriate template into your image directory:

- `example_registered_boxes.json` -> `C:\NickData\registered.json`
- `example_independent_boxes.json` -> `C:\NickData\independent.json`
- `example_whole_frame.json` -> `C:\NickData\whole.json`

All example rectangles are **illustrative coordinates, not test-day approvals**.
The older `example_jpeg_request.json` still works as a default registered geometry
check with no regions. Fill paths/hashes and metadata before processing.

Keep explicit mapping cam0=770, cam1=750, cam2=780. Canonical camera IDs0/1/2 with
swapped wavelength assignments are rejected. Paths resolve relative to the
manifest. Record actual exposure_us, gain, AE, original timestamp/time basis,
focus, filter/optical configuration, illumination and sequential capture span in
`acquisition`. Unknown values stay null; supplied metadata are recorded, not verified.

Fill SHA256 after editing paths (choose the manifest being prepared):

```powershell
$p = 'C:\NickData\registered.json'
$m = Get-Content -Raw -LiteralPath $p | ConvertFrom-Json
foreach ($c in $m.channels) {
    $f = if ([IO.Path]::IsPathRooted($c.path)) { $c.path } else { Join-Path (Split-Path $p) $c.path }
    $c.sha256 = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
}
[IO.File]::WriteAllText($p, ($m | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
```

Define regions from continuum750/780 views and physical holder/background features,
BEFORE inspecting spectral excess. Rectangles `[x0,y0,x1,y1]` are zero-based,
right/bottom exclusive; original image orientation retained. Source/background
must be nonempty, in bounds and disjoint within each image. Freeze choices and
record rationale, correspondence uncertainty and possible incomplete containment.

For registered mode, `provisional_regions` uses the shared770 grid. Any requested
pixel outside common support prevents region measurements; no trimming is performed.
Its optional independent-support pathway is unchanged (see README.md).

For independent mode, `independent_regions.cameras` binds each native rectangle
pair to camera_id AND wavelength. Record `selection_rationale`,
`correspondence_description` and each `source_containment_note`. Equal pixel counts
or independently entered coordinates do not prove the same physical coverage.
There is no automatic ROI selection, correspondence check or approval.

Whole-frame mode has no source/background fields. Incompatible non-null region or
eligibility fields are rejected rather than ignored. Native modes always remain
provisional even if the operator believes correspondence is good. The strict
linear-image path and independent eligibility contracts are unchanged.

## Commands (repository root)

### Draw native boxes with the optional Windows selector

The selector uses Python's **Tkinter/Tcl/Tk**, not OpenCV GUI windows. No Pillow
dependency is needed. The official Windows Python installation must include its
optional Tcl/Tk component (inherited by the virtual environment). Check first:

```powershell
.\.venv-optiona\Scripts\python.exe -m tkinter
```

Close the Tk test window. From the repository root, select the original triplet:

```powershell
$py = '.\.venv-optiona\Scripts\python.exe'
$d = 'C:\NickData\SetA_50us'
$event = '20261014_175114_156'
$images = @('--cam0', "$d\${event}_cam0_770nm.jpg", '--cam1', "$d\${event}_cam1_750nm.jpg", '--cam2', "$d\${event}_cam2_780nm.jpg")
& $py -B -m research.anchor_g2.box_selector select @images --rationale 'Physical holder and comparable background; selected before examining spectral excess' --output "$d\manual-boxes-v1.json"
```

For each camera, select yellow **SOURCE** or blue **BACKGROUND**, then drag a box.
Drag inside the selected box to move it or drag a corner to resize. Fit and 1:1
views plus scrollbars preserve original-pixel coordinates. Reset clears the current
camera. Empty, outside-image and overlapping boxes cannot be confirmed. Confirm
each camera explicitly; the preset is saved only after the third confirmation.
Closing early saves nothing. To redo an already confirmed camera, close and restart.
The provided reference coordinates are not preloaded or treated as approvals.

Reuse the preset on this triplet, or change `$event`/`$d` and rebuild `$images` for
a new triplet. Reinspect scene placement first: matching dimensions do not prove
matching physical coverage. Camera flags explicitly declare wavelengths; conflicting
`camN_NNNnm` filename tags are rejected. Untagged filenames require correct operator
mapping, which image content alone cannot verify.

```powershell
& $py -B -m research.anchor_g2.box_selector apply @images --preset "$d\manual-boxes-v1.json" --acknowledge-unverified-reuse --output "$d\manual-independent-v1.json"
& $py -B -m research.anchor_g2 "$d\manual-independent-v1.json" --exploratory-jpeg --processing-mode independent-boxes --output "$d\manual-result-v1.json"
```

Every output must be new; existing presets, manifests and results are refused.
Original JPEGs are opened read-only, decoded without EXIF orientation rotation,
and hashed. Presets store `[width,height]`, original-pixel rectangles (right/bottom
exclusive), mapping, rationale and unverified warnings. Application verifies each
camera's dimensions, recomputes hashes, and leaves acquisition fields null by default;
neither the folder name nor filename establishes exposure/gain. Optional `--metadata`
accepts a JSON file keyed by `"0"`, `"1"`, `"2"`, each containing only acquisition
fields from the example manifest, e.g. `{"0":{"exposure_us":50},"1":{"exposure_us":50},"2":{"exposure_us":50}}`.
Provide values only when documented; metadata remain user-supplied and unverified.
No spectral result is shown during selection, no correction is applied, and manual
confirmation never promotes scientific eligibility. The existing analysis command
still returns exit 2 for usable provisional UNKNOWN output.

Selector regression tests (in addition to the three suites above):

```powershell
& $py -B -m unittest research.anchor_g2.test_box_selector -v
```

```powershell
.\.venv-optiona\Scripts\python.exe -B -m research.anchor_g2 C:\NickData\registered.json --exploratory-jpeg --processing-mode registered-boxes --output C:\NickData\registered-result.json
.\.venv-optiona\Scripts\python.exe -B -m research.anchor_g2 C:\NickData\independent.json --exploratory-jpeg --processing-mode independent-boxes --output C:\NickData\independent-result.json
.\.venv-optiona\Scripts\python.exe -B -m research.anchor_g2 C:\NickData\whole.json --exploratory-jpeg --processing-mode whole-frame --output C:\NickData\whole-result.json
```

A manifest may set `processing_mode`; if both CLI and manifest specify it, they
must agree. Omitting both preserves registered-boxes. `--processing-mode` requires
`--exploratory-jpeg`. `--output` requires an existing parent directory and a NEW
file; existing files are refused (also protected by exclusive creation). No aligned
image or map files are written. Without `--output`, JSON prints to stdout.

## Read the JSON

`processing_mode` identifies the path; `channel_statistics` includes dimensions,
intensity ranges, clipping and acquisition metadata. Registered mode includes
matrices, acceptance and common support; native modes explicitly say NOT_REQUESTED
and have no common geometric support. No failed registration is forced through.

Without independent evidence, top-level P/I remain null, status UNKNOWN and exit2.
Usable `provisional.schema=PROVISIONAL_JPEG_DIAGNOSTICS` values can accompany exit2.
Exit0 only denotes the existing eligible numerical path, not scientific validation.

Registered `provisional.P_numerical`/`I_numerical` retain existing behavior. Frozen
P summaries are whole-common-valid-field; source_valid_count is ROI-specific,
not its mean/median. Frozen I reports gross/background/net signals; positive ratios
still have unresolved uncertainty and supported_ratio null.

Native `provisional.channels` reports each region's sums, means, pixel counts,
saturation and mask hash. `gross` (both modes) and `net` (independent only) report
signals, continuum, excess and numeric_ratio. Ratios are suppressed for nonpositive
continuum, continuum at floating-point roundoff scale, or saturation in measured
regions; no pixels are silently dropped. The roundoff bound is8*float64 epsilon*
max(1,sum absolute signals,cancellation scale), not a physical SNR/fire threshold.
Small positive values above that bound still have **unresolved physical uncertainty**.
Inspect net_to_gross_continuum and negative-channel flags. No supported ratio or
calibrated emission is claimed. Whole-frame totals retain clipped pixels and flag
saturation; dimensions can differ, but correspondence remains unknown.

`timing_seconds` separates input/decode/statistics, registration, measurement and
total. Native registration time is0. Total measures the evaluator, excluding Python
startup/imports and final JSON writing; small unclassified setup overhead remains.
Windows times are not measured Jetson performance. Registered mode also computes
P, so mode-total differences are not solely registration cost.

## Experimental limitations

Keep exposure, gain, AE, focus, filters and illumination fixed; record fire-off/on
close together with original timestamps. Use stationary features near the source;
a uniformly dark view may lack registration texture or meaningful continuum.
Historical captures were sequential over roughly2seconds per triplet. Record
actual timing; a shared timestamp is not evidence of simultaneous flame views.
JPEG gamma/ISP, compression, per-camera spectral response, vignetting and unknown
flat/dark compatibility remain confounders. This pathway applies no correction and
never assumes linear radiance. Positive D alone does not establish potassium or fire.

The three CLI paths were exercised on the existing200us October14 control triplet
20261014_155011_645 with fixed illustrative/historical regions. All produced UNKNOWN
research results. Registered numerical P/I matched the previously published smoke
output exactly. Native numerical differences are expected from sampling/region
placement; whole-frame gross and box-net results measure different things.
See THREE_MODE_VERIFICATION.md for measured values and one-run timing conditions.
