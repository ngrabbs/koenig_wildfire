# K-Line Wildfire Detection — Team Primer

## Purpose

This is a short, undergraduate-friendly explanation of *why* the centisat
payload uses three narrowband NIR filters near 762 / 766 / 770 nm to
detect wildfires from orbit. Read this before working on the payload
optics, the image-processing pipeline, or the ML side. It is not a
literature review — it's the minimum physics you need to talk
intelligently about what we're doing and why.

## 1. The detection problem

Wildfires are hot (~600–1200 °C in active flames) and emit broadband
thermal radiation, mostly in the mid- and long-wave infrared (3–14 µm).
The "obvious" way to spot a fire from orbit is a thermal IR imager
(this is what NASA's VIIRS and MODIS do).

The catch: thermal IR sensors for space are expensive, need cooling, and
are physically large. A 1U CubeSat can't carry one. So we need a
different signature — one that a cheap silicon CMOS sensor (which only
sees ~400–1000 nm) can pick up.

**The trick: burning vegetation has a chemical fingerprint in the
visible-NIR range** that thermal radiation alone does not have.

## 2. Why potassium?

When plant material burns, it releases sodium and potassium vapor into
the flame. These atoms get thermally excited and emit light at very
specific wavelengths — the same way a sodium-vapor streetlamp emits
that distinctive orange-yellow. This is **atomic emission spectroscopy**.

Potassium (K) is especially useful for wildfire detection because:

- **Vegetation is K-rich.** Pine needles, oak leaves, eucalyptus, and
  most temperate / boreal forest biomass contain ~1–3% potassium by dry
  mass. When they burn, a lot of K vapor is released.
- **The K I emission lines are bright and well-isolated.** Neutral
  potassium emits a strong "doublet" — two narrow lines very close
  together — at **766.49 nm and 769.90 nm**. These are easily resolved
  with off-the-shelf narrowband filters.
- **These lines sit in a clean atmospheric window.** The atmosphere is
  mostly transparent at 766–770 nm, so the emission gets to orbit.
- **Silicon CMOS sensors see them well.** 770 nm is firmly in the NIR
  sensitivity range of Sony's IMX-series sensors — about 25–35 % quantum
  efficiency once the IR-cut filter is removed.

The same physics is exploited by ground-based fire spotters
(historically) and by airborne fire mappers. It has also been proposed
for several CubeSat-class fire-detection missions (HOTSAT, FireSat
concept studies, etc.).

## 3. What a narrowband filter does

A narrowband interference filter passes only a tiny slice of the spectrum
— typically 1–10 nm full-width-half-max (FWHM) — and rejects everything
else by ~5 orders of magnitude.

If we point a camera with a 766 nm filter at a fire, the sensor sees:

- A *huge* signal from the K I emission line at 766.5 nm (fire-specific).
- A *small* signal from broadband sunlight reflected off the ground in
  the narrow ~1 nm window the filter passes (always present in daylight,
  zero at night).
- Essentially nothing from any other source.

The ratio of "fire signal" to "everything else" is way better than for a
broadband image of the same scene — that's the point of narrowband
imaging.

## 4. Why three filters, not one?

A single 766 nm channel tells you "this pixel is bright at 766 nm."
That's ambiguous. It could be:

- A fire emitting K (what we want).
- A bright reflective surface (snow, clouds, desert, glint off water).
- A sensor hot pixel.
- A bright industrial light source.

To disambiguate, we use **differential measurement**: compare brightness
at the K emission wavelengths against brightness at a nearby wavelength
where K *doesn't* emit. If something is bright at the K lines but
*not* in the off-line reference, it's K emission. If it's bright in
both, it's just a generally bright thing — not a fire.

Centisat uses three narrowband channels:

| Filter | Wavelength | What it sees | Role |
|---|---|---|---|
| Camera #0 | 766 nm | K I emission line #1 (766.49 nm) | "On-line" — high when K is emitting |
| Camera #1 | 770 nm | K I emission line #2 (769.90 nm) | "On-line" — high when K is emitting |
| Camera #2 | 762 nm | Off-line reference (no K line here) | "Off-line" — baseline brightness from non-K sources |

Two on-line channels (not one) because:
- The two K I lines have slightly different intensity ratios at different
  flame temperatures — having both gives a redundancy check and a
  temperature estimator.
- It improves SNR — we're averaging two independent K-emission samples.
- If one camera has a hot pixel or filter contamination, the other can
  flag the inconsistency.

## 5. The detection algorithm — first principles

For every pixel in the registered three-channel image, compute:

```
fire_index(pixel) = (signal_766 + signal_770) / (2 × signal_762)
```

- **No fire:** all three channels see roughly the same reflected sunlight
  spectrum across the small ~10 nm window. Ratio ≈ 1.
- **Fire present:** the K emission adds signal to the 766 and 770 channels
  but does *not* affect 762. Ratio rises above 1.
- **Threshold:** declare "candidate fire pixel" when ratio exceeds some
  empirically chosen threshold (probably 1.3–2.0; this will need to be
  calibrated against ground-truth data).

This is Stage 1. It's pixel-wise, runs in microseconds, and rejects most
of the obvious false positives (anything broadband-bright).

## 6. A small atmospheric bonus from picking 762 nm

The 762 nm reference happens to land on the edge of the **O₂ A-band**,
an atmospheric oxygen absorption feature centered around 761 nm. This
means at 762 nm, reflected sunlight gets attenuated by O₂ in the column
of air between the ground and the satellite.

This doesn't help Stage 1 detection directly (we're computing a ratio,
which mostly cancels common-mode atmospheric effects). But it does
provide a useful side-channel:

- During the day, the 762 channel sees mostly Rayleigh-scattered sky
  light, with very little contribution from ground reflection.
- This makes the 762 channel a *cleaner* reference than picking
  something like 750 nm or 800 nm would have been.
- It also gives us a free atmospheric-correction signal if we ever want
  to estimate path-integrated O₂ column density.

In short: the 762 nm choice is good, but for subtler reasons than just
"it's between the K lines." Future filter selection should keep this
property in mind.

## 7. What this technique does *not* do

To be honest about scope:

- **It does not give temperature.** Two K lines do constrain flame
  temperature weakly, but it's not a calorimeter. Don't promise
  "temperature mapping" in the proposal.
- **It does not see through optically thick clouds.** Anything that
  blocks NIR (a dense cumulus deck, for example) hides the fire.
  Thin cirrus or smoke is partially penetrated.
- **It will produce false positives** on industrial flares, biomass
  power plants, and (rarely) certain volcanic emissions. These also
  emit K. Stage 2 ML classification is what discriminates "wildfire"
  from "industrial source."
- **Spatial resolution is bad.** A 1U CubeSat with a realistic lens
  gives ~30–100 m ground sample distance from LEO. Fires must be
  ≥ a few pixels wide to detect reliably — say, ~1 hectare minimum.

## 8. Where the ML comes in (Stage 2)

Stage 1 produces a binary mask: "this pixel is a candidate fire."
Stage 2's job is to look at the *spatial pattern* of candidate pixels
and classify the whole scene:

- **Real wildfire:** irregular blob, edges that look like terrain,
  often with smoke signatures in adjacent broader-band channels.
- **Industrial flare:** small, sharp point source, geographically
  fixed (we know where steel mills and oil refineries are).
- **Sun glint:** specular pattern, moves with sun angle.
- **Sensor artifact:** spatially uncorrelated, often single pixels.

This is a CNN classification problem on the 3-channel composite plus
geographic context — a good fit for the Orin Nano's GPU, and the
mission-relevant ML work.

## 9. Practical follow-ups

If you're working on this payload, the things worth learning more about:

- **Spectroscopy 101:** atomic emission, Doppler/pressure broadening of
  spectral lines, what "1 nm FWHM" means in practice.
- **The IMX296 datasheet:** specifically the QE curve at 760–770 nm,
  and the impact of removing the on-chip color filter array (Bayer
  matrix) on per-pixel spectral response.
- **Atmospheric NIR transmittance:** MODTRAN or libRadtran simulations
  showing what fraction of the K emission reaches LEO at various solar
  zenith angles and atmospheric water/aerosol loadings.
- **Existing fire-detection literature:** Vodacek et al. (2002),
  Amici et al. (2011) on airborne K-line detection. The HOTSAT and
  FireSat concept studies are good entry points to the satellite side.

## 10. One-sentence elevator pitch

> *Burning vegetation releases potassium vapor that emits a unique
> spectral fingerprint at 766 and 770 nm; we take three narrowband
> images of the same scene and compute a pixel-wise ratio to detect that
> fingerprint over the background, then use a CNN to reject false
> positives.*

If you can say that in an interview, you understand the payload.

