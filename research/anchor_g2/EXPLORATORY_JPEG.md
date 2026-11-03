# Explicit exploratory JPEG mode

For user-defined provisional rectangles and a new output JSON file, see
[NICK_TEST_DAY.md](NICK_TEST_DAY.md) and `example_jpeg_request.json`.
Provisional numerical values are separate from independently eligible P/I fields.

```powershell
python -B -m research.anchor_g2 C:\NickData\jpeg_request.json --exploratory-jpeg
python -B -m unittest research.anchor_g2.test_adapter research.anchor_g2.test_exploratory_jpeg -v
```

Both the CLI flag and request schema/mode are required. Without the flag, the
existing strict pathway still rejects JPEG requests. Reports always retain
NOT_CALIBRATED/UNKNOWN; they never classify fire or fit a threshold.

Minimal request (replace hashes and paths, do not treat these placeholders as data):

```json
{
  "schema": "EXPLORATORY_JPEG_TRIPLET_V1",
  "mode": "EXPLORATORY_JPEG",
  "correction_policy": "NONE_UNVERIFIED",
  "calibration_reason": "Capture settings and JPEG response compatibility with the available calibration are not established",
  "channels": [
    {"path":"770.jpg","sha256":"REPLACE","camera_id":"0","wavelength_nm":770},
    {"path":"750.jpg","sha256":"REPLACE","camera_id":"1","wavelength_nm":750},
    {"path":"780.jpg","sha256":"REPLACE","camera_id":"2","wavelength_nm":780}
  ],
  "eligibility": null
}
```

A channel may instead specify `archive` and exact `member` plus SHA256, camera
and wavelength. ZIP members are read directly without extraction; duplicate or
oversized members are rejected. Paths are relative to the request. Optional
`acquisition` fields are recorded as provided, including unknowns.

JPEGs decode at full resolution. BGR encoding uses the frozen OpenCV BGR2GRAY
convention. Full-frame min/max/mean/percentiles, zero and clipping fractions are
reported in **JPEG code values**, not linear radiance. Gamma, ISP processing,
compression, cross-camera response and exposure/gain remain confounders.

No matching JPEG-specific correction provenance currently exists. This minimum
extension supports only explicitly uncorrected JPEGs: a supplied correction is
rejected, not silently ignored. Unit gain/zero offset are merely the existing
fixed-point encoder for unchanged code values, not calibration estimates. A
future corrected-JPEG pathway requires explicit response/settings/provenance
compatibility and separate implementation; unchanged dimensions are insufficient.

The same frozen phase preparation/fit, masks, sampling and numerical equations
are reused. Optional registration telemetry preserves rejected preparation/pair
results; no fallback forces acceptance. Common counts/fractions are reported
only when sampling was reached. This does not change strict numerical behavior.

P/I use the existing hash-bound `INDEPENDENT_SUPPORT_V1` contract, source and
background boolean NPY masks described in README. Supply `eligibility`,
`source_mask` and `background_mask` references only when independently justified.
Exact image hashes/transforms, optical/synchronization support and independent
alignment evidence must agree. Without that evidence, geometry/intensity may be
available but P/I stay null. Existing heuristic/native apertures cannot be
silently substituted for evidence on the shared 770 grid. Eligible numerical
outputs are relabeled JPEG_CODE_VALUE_PROXY; no calibrated physical interpretation
or supported uncertainty ratio is claimed. I remains diagnostic when P is
ineligible but its own support requirements pass.

## Local six-event pilot (development only)

The two original archives contain 310 events/930 JPEGs; the 120 pinned lab-log
JPEGs are exact duplicates. The pilot was selected before analysis by historical
scene/exposure: E036, E132, E147, October14 154710_733 (400-us provisional no-fire),
162825_097 (200-us fence fire), 175114_156 (50-us green fire). Historical labels
are contextual, not new predictions or independently validated ground truth.

Five engineering registration acceptances, one dim/flat rejection. Accepted
common fractions span 0.764990–0.787213. **Zero eligible P/I measurements** because
independent alignment and source/background evidence for these transforms was
unavailable. No calibration was applied and no thresholds were fitted. This
establishes limited registration feasibility, not spectral discrimination or
robustness of integrated brightness.

The local-only helper (not part of the published test-day package)
`run_jpeg_pilot.py PAYLOAD NEW_OUTPUT` inventories the original archives and runs
only these six preselected events, each in a child with a 60-second timeout.
It refuses existing output directories and checks before/after source hashes.
It is a local research helper, not a hard memory/storage supervisor or B3 run.
