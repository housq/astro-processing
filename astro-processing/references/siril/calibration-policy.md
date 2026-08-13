# Calibration and grouping policy

Use metadata conservatively. The objective is not to consume every file; it is to build defensible groups and quarantine ambiguity.

## Classification order

1. FITS `IMAGETYP` or equivalent header value
2. Recognized directory name
3. Leading filename token
4. Quarantine

Recognize light/object/science, dark, flat, bias/offset, and dark-flat/flat-dark aliases. Do not infer a type from exposure alone: a short exposure is not proof of a bias or flat.

Camera RAW often lacks an astronomy frame-type tag. Prefer conventional directories or filename prefixes. If neither exists, request clarification or a mapping; never mix unknown RAW files into a sequence.

## Light grouping

Split lights when any of these differ materially:

- target
- session date
- camera or sensor geometry
- Bayer pattern
- binning
- gain/ISO or offset
- filter
- exposure duration

Separate targets unconditionally. For a deliberate multi-night integration, let the user opt into merging compatible session groups after each session has been calibrated and inspected independently.

## Mandatory compatibility

Reject a calibration candidate when known values disagree for camera, width, height, binning, gain/ISO, offset, or Bayer pattern. Reject a flat/dark-flat with a known incompatible filter. Missing metadata lowers confidence; it does not prove incompatibility or compatibility.

### Bias

Match camera readout mode, dimensions, binning, gain/ISO, and offset. Temperature is usually less important than for darks but should remain consistent when the camera behavior warrants it.

### Dark

Match exposure, gain/ISO, offset, binning, dimensions, and readout mode. Prefer temperature within 2 °C; use 5 °C as the default automatic ceiling. Beyond 5 °C, do not select automatically.

Dark optimization is not a universal cure. Scaling can fail with CMOS amp glow, non-linear patterns, or changing hot pixels. If comparing a mismatched-dark branch, require a bias, label it, and compare residual pattern noise against a no-dark branch.

### Flat

Match camera geometry, binning, gain/ISO, optical train, focus, rotation, and filter. Date proximity is useful but secondary to an unchanged optical path. Inspect median level and saturation; reject clipped or extremely dim flats.

### Dark-flat

Prefer a dark-flat matching the flat exposure and readout settings. When both bias and matching dark-flat are available, prefer the method appropriate to the camera and keep the chosen calibration formula consistent.

## Minimum counts and warnings

Treat fewer than five lights as high risk. More calibration frames generally improve master rejection, but correctness of settings matters more than count. Record missing master types as explicit degradation rather than inventing substitutes.

## Cleaning lights

After calibration and registration, evaluate:

- registration success
- weighted FWHM/FWHM
- roundness/eccentricity
- star count
- background level and noise
- clouds, aircraft/satellite trails, wind shake, guiding jumps, frost, and dew

Use robust sigma filters as an initial proposal. Preserve the original inclusion set in logs. If combined automated criteria reject more than 30%, relax and compare; do not allow several correlated filters to silently discard most integration time.

Pixel rejection during stacking handles isolated transient trails better than discarding an otherwise strong exposure. Reject a whole light only when its global quality or uncorrectable structure harms the result.
