# Three-mode implementation verification — 2026-11-04

Local, uncommitted extension of a65e81f076946181e5f59605df5e8d5b7ecccafc on
research/anchor-g2-integrated-brightness. No commit or push authorized/performed.

## Implementation

- `registered-boxes`: existing JPEG workflow/default, same frozen registration,
  sampling, support gates, provisional P/I arithmetic and independent eligibility.
  Only mode routing, timing and canonical camera mapping validation added.
- `independent-boxes`: original decoded native arrays, explicit per-camera boxes,
  per-camera source count/background mean subtraction. No registration/warp and
  no P or strict eligibility. Uses frozen `_bounded_sum`; the existing I function
  assumes a shared mask/count and cannot represent unequal native apertures, so
  a small scalar summary applies the same C=(S750+2*S780)/3 and D=S770-C algebra.
  Matching-aperture parity is tested against the unchanged I function.
- `whole-frame`: all native pixels summed, no regions, cropping, warping or
  background subtraction. Different dimensions are reported, not reconciled.

All native outputs PROVISIONAL_JPEG_DIAGNOSTICS, NOT_CALIBRATED/UNKNOWN. Numerical
roundoff and saturation guards do not confer scientific validity; supported_ratio
remains null. No new empirical SNR/fire threshold. Registered behavior unchanged.

## Tests

```powershell
python -B -m unittest research.anchor_g2.test_adapter research.anchor_g2.test_exploratory_jpeg research.anchor_g2.test_modes -v
```

**17 tests passed**, final run0.884s. Coverage: frozen SHA256 identities, exact I
arithmetic extraction/parity, default-vs-explicit registered equality, generated
registration acceptance/rejection, strict eligibility preservation, JPEG opt-in,
all native routing with mocked register/warp asserted unused, per-camera native
coordinates, unequal dimensions/counts, exact totals/means, zero/negative/near-
roundoff continuum, saturation, bad mapping/missing file, conflicting selections,
missing/overlapping regions, UNKNOWN, read-only inputs and output overwrite refusal.

No formal24-case B3 qualification or hardware test was run.

## One bounded real-JPEG smoke comparison

Input: documented200us October14 control20261014_155011_645. Exact input manifest,
original ZIP member hashes, three actual requests/results, commands and preservation
records are in:

`C:\Users\jacob\OneDrive\Desktop\Capstone_Payload\Phase7J_Three_Mode_Test_Package_Verification_v1`

Each CLI invocation had a60-second subprocess timeout. Requests were instantiated
from the three supplied templates, using the existing explicit archive-member
references and acquisition metadata. Independent native boxes were fixed from the
previous native holder/continuum review; correspondence and containment remain
unverified. No spectral tuning, new source labels or broader dataset execution.

| Mode | Quantity | C | D | Numeric D/C | Evaluator s | Registration s | Process wall s |
|---|---|---:|---:|---:|---:|---:|---:|
| registered-boxes | background-subtracted | -12704.30599 | 27584.30599 | null | .710925 | .437833 | 1.764373 |
| independent-boxes | background-subtracted | -12757.33333 | 27637.33333 | null | .171562 | 0 | .666936 |
| whole-frame | gross entire image | 10885815.66667 | -1031846.66667 | -.09478818 | .152668 | 0 | .966974 |

All commands exited2 with status UNKNOWN and produced their requested JSON.
Registered geometry, P_numerical and I_numerical exactly matched the previously
published package's real-JPEG smoke result. Its fixed-point sampling is unchanged;
minor differences from historical standalone exploratory scripts are not changes
in this implementation. Box net and whole-frame gross are different measurements;
no fire inference follows from their signs or magnitude. Native boxes approximate
physical correspondence, so this is not a scientific method-equivalence trial.

Timings: one Windows invocation per mode, in table order, Python3.12.14,
NumPy2.3.5/OpenCV5.0.0. Evaluator total excludes interpreter/import startup and JSON
writing; process wall includes them. Input/decode/percentile statistics and separate
measurement times are retained in each JSON. Different startup/cache/thread
conditions and registered P computation confound mode-total comparisons. These are
not measured Jetson performance, benchmarks with error bars, or isolated cost ratios.

## Preservation and exact file scope

Changed existing files:
- `research/anchor_g2/__main__.py`
- `research/anchor_g2/exploratory_jpeg.py`
- `research/anchor_g2/NICK_TEST_DAY.md`
- `research/anchor_g2/EXPLORATORY_JPEG.md`

New files:
- `research/anchor_g2/native_modes.py`
- `research/anchor_g2/test_modes.py`
- `research/anchor_g2/example_registered_boxes.json`
- `research/anchor_g2/example_independent_boxes.json`
- `research/anchor_g2/example_whole_frame.json`
- `research/anchor_g2/THREE_MODE_VERIFICATION.md`

New example JSON files are present locally but ignored by the repository's global
*.json rule; they will need explicit inclusion if publication is later authorized.
No staging was performed. The preexisting untracked run_jpeg_pilot.py is preserved.

Before/after hashes in verification.json confirm the original October14 ZIP,
calibration archive, prior request/result, local pilot runner, copied frozen modules,
extracted I arithmetic, adapter.py and execution policy unchanged during smoke.
Calibration SHA256 remains0649ecedd1d68e31f2cc58bd4808946c8a360d157b22db5debfe2b227df58b51.
Git diff confirms no changes outside research/anchor_g2 and no edits to frozen/,
adapter.py, integrated_arithmetic.py or execution_policy.json. git diff --check
passes. Operational branch stays7f907a2d243a2a17e87b009a62271ee21e1b812f.

No implementation blocker remains. Independent radiometric/region/timing validity
is unresolved by design. Await human review; no publication or additional experiment.
